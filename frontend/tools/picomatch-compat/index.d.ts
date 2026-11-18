declare function picomatch(pattern: string | string[], options?: Record<string, any>): ((input: string) => boolean) & Record<string, any>;
export default picomatch;
export function isMatch(input: string, patterns: string | string[], options?: Record<string, any>): boolean;
export function makeRe(pattern: string, options?: Record<string, any>): RegExp;
export function scan(input: string, options?: Record<string, any>): Record<string, any>;
export function parse(pattern: string, options?: Record<string, any>): Record<string, any>;
