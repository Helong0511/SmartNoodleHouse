"""整理 sql/data.sql：表/字段统一反引号，语句压紧以节省空间。"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "sql" / "data.sql"

KEYWORDS = {
    "ADD", "ALL", "ALTER", "AND", "ANY", "AS", "ASC", "AVG", "BETWEEN", "BY", "CASE",
    "CAST", "COUNT", "CROSS", "CURRENT_TIMESTAMP", "DEFAULT", "DELETE", "DESC",
    "DISTINCT", "DROP", "DUAL", "ELSE", "END", "EXISTS", "FALSE", "FOR", "FOREIGN",
    "FOREIGN_KEY_CHECKS", "FROM", "GROUP", "HAVING", "IF", "IN", "INDEX", "INNER",
    "INSERT", "INTO", "IS", "JOIN", "KEY", "LEFT", "LIKE", "LIMIT", "MAX", "MIN",
    "NAMES", "NOT", "NULL", "ON", "OR", "ORDER", "OUTER", "PRIMARY", "REFERENCES",
    "RIGHT", "SELECT", "SET", "SHOW", "SUM", "TABLE", "THEN", "TRUE", "TRUNCATE",
    "UNION", "UNIQUE", "UPDATE", "USE", "USING", "VALUES", "WHEN", "WHERE", "WITH",
    "XOR",
}

TABLE_CONTEXT = {"FROM", "JOIN", "UPDATE", "USE", "TRUNCATE_TABLE"}
SET_VARS = {"NAMES", "FOREIGN_KEY_CHECKS", "CHARACTER_SET_CLIENT", "CHARACTER_SET_RESULTS", "COLLATION_CONNECTION"}


def protect_strings(sql: str) -> tuple[str, list[str]]:
    parts: list[str] = []
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        if sql[i] == "'":
            j = i + 1
            buf = ["'"]
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    buf.append("''")
                    j += 2
                    continue
                if sql[j] == "'":
                    buf.append("'")
                    j += 1
                    break
                buf.append(sql[j])
                j += 1
            parts.append("".join(buf))
            out.append(f"__STR{len(parts) - 1}__")
            i = j
            continue
        out.append(sql[i])
        i += 1
    return "".join(out), parts


def restore_strings(sql: str, parts: list[str]) -> str:
    return re.sub(r"__STR(\d+)__", lambda m: parts[int(m.group(1))], sql)


def backtick(name: str) -> str:
    if not name or name.upper() in KEYWORDS:
        return name
    if name.startswith("__STR"):
        return name
    if re.fullmatch(r"\d+(\.\d+)?", name):
        return name
    return f"`{name}`"


def rewrite_sql(sql: str) -> str:
    """去旧反引号后，按上下文给表/字段加反引号。"""
    sql = sql.replace("`", "")
    # tokenize: keep separators
    parts = re.findall(r"[A-Za-z_][\w]*|\d+\.\d+|\d+|__STR\d+__|\S|\s+", sql)
    out: list[str] = []
    prev_kw = ""
    expect_alias = False
    in_insert_cols = False
    paren_depth = 0
    insert_col_depth = -1

    i = 0
    while i < len(parts):
        tok = parts[i]
        if not tok:
            i += 1
            continue

        if tok.isspace():
            out.append(tok)
            i += 1
            continue

        upper = tok.upper()

        if tok == "(":
            paren_depth += 1
            if prev_kw == "INTO_TABLE":
                in_insert_cols = True
                insert_col_depth = paren_depth
            out.append(tok)
            prev_kw = "("
            i += 1
            continue

        if tok == ")":
            if in_insert_cols and paren_depth == insert_col_depth:
                in_insert_cols = False
                insert_col_depth = -1
            paren_depth = max(0, paren_depth - 1)
            out.append(tok)
            prev_kw = ")"
            i += 1
            continue

        if re.fullmatch(r"[A-Za-z_][\w]*", tok) or tok.startswith("__STR"):
            if upper in KEYWORDS and not tok.startswith("__STR"):
                out.append(tok)
                if upper == "TABLE" and prev_kw == "TRUNCATE":
                    prev_kw = "TRUNCATE_TABLE"
                elif upper == "SET":
                    # UPDATE t SET col=...  vs  SET NAMES / SET FOREIGN_KEY_CHECKS
                    if prev_kw in {"TABLE", "ALIAS", "INTO_TABLE", "QUAL", "IDENT"}:
                        prev_kw = "UPDATE_SET"
                    else:
                        prev_kw = "SET"
                elif upper == "DELETE":
                    prev_kw = "DELETE"
                elif upper == "NAMES":
                    prev_kw = "NAMES"
                else:
                    prev_kw = upper
                expect_alias = False
                i += 1
                continue

            j = i + 1
            while j < len(parts) and parts[j].isspace():
                j += 1
            if j < len(parts) and parts[j] == ".":
                out.append(backtick(tok))
                prev_kw = "QUAL"
                expect_alias = False
                i += 1
                continue

            if prev_kw == ".":
                out.append(backtick(tok))
                prev_kw = "IDENT"
                i += 1
                continue

            if in_insert_cols:
                out.append(backtick(tok))
                prev_kw = "COL"
                i += 1
                continue

            if prev_kw == "UPDATE_SET":
                out.append(backtick(tok))
                prev_kw = "UPDATE_COL"
                i += 1
                continue

            # SET NAMES utf8mb4 / SET FOREIGN_KEY_CHECKS = 0
            if prev_kw == "SET":
                out.append(tok)
                prev_kw = "SET_VAR"
                i += 1
                continue
            if prev_kw in {"SET_VAR", "NAMES"}:
                out.append(tok)
                prev_kw = "IDENT"
                i += 1
                continue

            # DELETE alias FROM ...
            if prev_kw == "DELETE":
                out.append(tok)  # alias 不加反引号（FROM 是关键字会先走 KEYWORDS）
                prev_kw = "DELETE_ALIAS"
                i += 1
                continue

            if expect_alias:
                out.append(tok)
                prev_kw = "ALIAS"
                expect_alias = False
                i += 1
                continue

            if prev_kw == "INTO":
                out.append(backtick(tok))
                prev_kw = "INTO_TABLE"
                expect_alias = False
                i += 1
                continue

            if prev_kw in TABLE_CONTEXT:
                ctx = prev_kw
                out.append(backtick(tok))
                prev_kw = "TABLE"
                expect_alias = ctx in {"FROM", "JOIN", "UPDATE"}
                i += 1
                continue

            if prev_kw == "TABLE":
                out.append(tok)
                prev_kw = "ALIAS"
                expect_alias = False
                i += 1
                continue

            if prev_kw == "AS":
                out.append(tok)
                prev_kw = "ALIAS"
                i += 1
                continue

            out.append(backtick(tok))
            prev_kw = "IDENT"
            i += 1
            continue

        if tok == ".":
            out.append(tok)
            prev_kw = "."
            i += 1
            continue

        if tok == ",":
            out.append(tok)
            # stay in insert cols / don't clear TABLE alias expectation wrongly
            if prev_kw not in {"COL"} and not in_insert_cols:
                prev_kw = ","
            i += 1
            continue

        out.append(tok)
        prev_kw = tok
        i += 1

    text = "".join(out)
    # cleanup doubled spaces from token join
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text


def compact_statement(stmt: str) -> str:
    s = stmt.strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\s*;\s*$", "", s)
    # 当前库已 USE，去掉多余库名前缀以省空间
    s = re.sub(r"INSERT INTO `smart_noodle_house`\.", "INSERT INTO ", s)
    s = re.sub(r"UPDATE `smart_noodle_house`\.", "UPDATE ", s)
    s = re.sub(r"DELETE FROM `smart_noodle_house`\.", "DELETE FROM ", s)
    return s + ";"


def process(text: str) -> str:
    lines = text.splitlines()
    out_lines: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        raw = "\n".join(buf)
        protected, parts = protect_strings(raw)
        rewritten = rewrite_sql(protected)
        restored = restore_strings(rewritten, parts)
        # split statements
        p2, parts2 = protect_strings(restored)
        for piece in p2.split(";"):
            piece = piece.strip()
            if not piece:
                continue
            stmt = restore_strings(piece, parts2)
            # skip pure comment leftovers
            if stmt.lstrip().startswith("--"):
                out_lines.append(stmt.strip())
                continue
            out_lines.append(compact_statement(stmt))
        buf = []

    for line in lines:
        st = line.strip()
        if not st:
            continue
        if st.startswith("--"):
            flush()
            if out_lines and out_lines[-1] != "" and not out_lines[-1].startswith("--"):
                out_lines.append("")
            out_lines.append(st)
            continue
        buf.append(line)
    flush()

    result = "\n".join(out_lines).strip() + "\n"
    if "SET FOREIGN_KEY_CHECKS = 0" in result and "SET FOREIGN_KEY_CHECKS = 1" not in result:
        result = result.rstrip() + "\n\nSET FOREIGN_KEY_CHECKS = 1;\n"
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def main() -> None:
    original = SRC.read_text(encoding="utf-8")
    formatted = process(original)
    SRC.write_text(formatted, encoding="utf-8", newline="\n")
    print(f"lines {original.count(chr(10)) + 1} -> {formatted.count(chr(10)) + 1}")
    print(f"chars {len(original)} -> {len(formatted)}")
    # quick samples
    for needle in ["INSERT INTO `roles`", "FROM DUAL", "FROM `roles`", "`code`"]:
        print(needle, "OK" if needle in formatted else "MISSING")
    # should not backtick DUAL
    print("bad DUAL tick", "`DUAL`" in formatted)
    print("bare INSERT", len(re.findall(r"INSERT INTO [A-Za-z_]", formatted)))


if __name__ == "__main__":
    main()
