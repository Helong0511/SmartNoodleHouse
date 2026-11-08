use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use once_cell::sync::Lazy;
use rand::{rngs::SysRng, TryRng};
use serde_json::{Map, Value};
use std::{
    cell::RefCell,
    collections::HashMap,
    env,
    ffi::{CStr, CString},
    os::raw::{c_char, c_uchar},
    panic::{catch_unwind, AssertUnwindSafe},
    sync::RwLock,
    thread,
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

const VERSION: u8 = 1;
const ABI_VERSION: u32 = 1;
const NONCE_LEN: usize = 12;
const TAG_LEN: usize = 16;
const HEADER_LEN: usize = 1 + 1 + NONCE_LEN;
const MIN_TOKEN_BYTES: usize = HEADER_LEN + TAG_LEN;
const MAX_TOKEN_CHARS: usize = 8192;
const MAX_PAYLOAD_CHARS: usize = 4096;
const MAX_TTL_SECONDS: u64 = 365 * 24 * 3600;
const MAX_FUTURE_SKEW: i64 = 300;
const MAX_TOKEN_AGE_SECONDS: i64 = 365 * 24 * 3600;
const MAX_KEYS: usize = 32;
const MAX_FIELDS: usize = 64;
const AAD_PREFIX: &[u8] = b"ad-tracking-engine:v1";

/// Best-effort compiler-resistant clearing for fixed-size secret buffers.
/// This reduces accidental key retention; it is not a guarantee against a
/// privileged debugger or memory forensics on the host.
#[inline(never)]
fn secure_zeroize(bytes: &mut [u8]) {
    for byte in bytes {
        unsafe {
            std::ptr::write_volatile(byte, 0);
        }
    }
    std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
}

// 密钥副本使用 RAII 清理：无论后续校验、初始化或加解密在哪一步失败，离开作用域都会清零。
struct SecretKey([u8; 32]);

impl Drop for SecretKey {
    fn drop(&mut self) {
        secure_zeroize(&mut self.0);
    }
}

pub const OK: i32 = 0;
pub const ERR_NULL_INPUT: i32 = 1;
pub const ERR_INVALID_ARGUMENT: i32 = 2;
pub const ERR_KEY_INVALID: i32 = 3;
pub const ERR_KEY_NOT_FOUND: i32 = 4;
pub const ERR_CURRENT_KEY_NOT_FOUND: i32 = 5;
pub const ERR_TOKEN_INVALID: i32 = 6;
pub const ERR_TOKEN_TOO_LARGE: i32 = 7;
pub const ERR_PAYLOAD_INVALID: i32 = 8;
pub const ERR_EXPIRED: i32 = 9;
pub const ERR_FUTURE_TIME: i32 = 10;
pub const ERR_INTERNAL: i32 = 100;

#[derive(Default)]
struct State {
    keys: HashMap<u8, [u8; 32]>,
    current_kid: Option<u8>,
}

// 商业发布加固：DLL 卸载时主动清零密钥内存。
// 注意：客户端 DLL 内的密钥仍然不能视为绝对保密，核心长期密钥应放服务端/KMS。
impl Drop for State {
    fn drop(&mut self) {
        for key in self.keys.values_mut() {
            secure_zeroize(key);
        }
        self.keys.clear();
        self.current_kid = None;
    }
}

static STATE: Lazy<RwLock<State>> = Lazy::new(|| RwLock::new(State::default()));

thread_local! {
    static LAST_ERROR: RefCell<Option<CString>> = const { RefCell::new(None) };
}

#[derive(Clone, Copy)]
struct PerformanceProfile {
    cpu_count: usize,
    recommended_workers: usize,
    recommended_batch: usize,
}

static PERFORMANCE: Lazy<PerformanceProfile> = Lazy::new(detect_performance_profile);

fn set_error(message: impl AsRef<str>) {
    let sanitized = message.as_ref().replace('\0', " ");
    let c = CString::new(sanitized).unwrap_or_else(|_| CString::new("internal error").unwrap());
    LAST_ERROR.with(|e| *e.borrow_mut() = Some(c));
}

fn clear_error() {
    LAST_ERROR.with(|e| *e.borrow_mut() = None);
}

fn now_unix() -> Result<i64, i32> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| ERR_INTERNAL)?;

    i64::try_from(duration.as_secs()).map_err(|_| ERR_INTERNAL)
}

fn cstr<'a>(ptr: *const c_char) -> Result<&'a CStr, i32> {
    if ptr.is_null() {
        set_error("null input");
        return Err(ERR_NULL_INPUT);
    }
    // SAFETY: the C ABI contract requires a valid NUL-terminated string.
    Ok(unsafe { CStr::from_ptr(ptr) })
}

fn cstr_string(ptr: *const c_char, max: usize, what: &str) -> Result<String, i32> {
    let c = cstr(ptr)?;
    let bytes = c.to_bytes();
    if bytes.len() > max {
        set_error(format!("{what} too large"));
        return Err(if what == "token" {
            ERR_TOKEN_TOO_LARGE
        } else {
            ERR_PAYLOAD_INVALID
        });
    }
    std::str::from_utf8(bytes).map(str::to_owned).map_err(|_| {
        set_error(format!("{what} is not valid UTF-8"));
        ERR_INVALID_ARGUMENT
    })
}

fn write_out(out: *mut *mut c_char, value: String) -> Result<(), i32> {
    if out.is_null() {
        set_error("output pointer is null");
        return Err(ERR_NULL_INPUT);
    }
    let c = CString::new(value).map_err(|_| {
        set_error("output contains NUL");
        ERR_INTERNAL
    })?;
    // SAFETY: ownership is transferred to the caller, which must call tracking_free.
    unsafe {
        *out = c.into_raw();
    }
    Ok(())
}

fn current_key() -> Result<(u8, [u8; 32]), i32> {
    let state = STATE.read().map_err(|_| {
        set_error("state lock poisoned");
        ERR_INTERNAL
    })?;
    let kid = state.current_kid.ok_or_else(|| {
        set_error("current KID is not configured");
        ERR_CURRENT_KEY_NOT_FOUND
    })?;
    let key = *state.keys.get(&kid).ok_or_else(|| {
        set_error("current KID has no configured key");
        ERR_CURRENT_KEY_NOT_FOUND
    })?;
    Ok((kid, key))
}

fn key_for(kid: u8) -> Result<[u8; 32], i32> {
    let state = STATE.read().map_err(|_| {
        set_error("state lock poisoned");
        ERR_INTERNAL
    })?;
    state.keys.get(&kid).copied().ok_or_else(|| {
        // External callers should normally map this to a generic invalid-token response.
        set_error("unknown KID");
        ERR_KEY_NOT_FOUND
    })
}

fn decode_hex_key(s: &str) -> Result<[u8; 32], i32> {
    if s.len() != 64 {
        set_error("AES-256 key must be exactly 64 hex characters");
        return Err(ERR_KEY_INVALID);
    }
    let bytes = hex::decode(s).map_err(|_| {
        set_error("AES-256 key is not valid hexadecimal");
        ERR_KEY_INVALID
    })?;
    let mut key = [0u8; 32];
    key.copy_from_slice(&bytes);
    Ok(key)
}

fn strict_payload_enabled() -> bool {
    match env::var("AD_TRACKING_STRICT_PAYLOAD") {
        Ok(v) => !matches!(
            v.trim().to_ascii_lowercase().as_str(),
            "0" | "false" | "no" | "off"
        ),
        Err(_) => true,
    }
}

fn validate_string_field(
    obj: &Map<String, Value>,
    name: &str,
    min: usize,
    max: usize,
) -> Result<(), i32> {
    let value = obj.get(name).and_then(Value::as_str).ok_or_else(|| {
        set_error(format!("{name} must be a string"));
        ERR_PAYLOAD_INVALID
    })?;
    if value.len() < min || value.len() > max {
        set_error(format!("{name} length invalid"));
        return Err(ERR_PAYLOAD_INVALID);
    }
    Ok(())
}

fn validate_payload(payload: &Value, now: i64, check_expiration: bool) -> Result<(), i32> {
    let obj = payload.as_object().ok_or_else(|| {
        set_error("payload must be a JSON object");
        ERR_PAYLOAD_INVALID
    })?;

    if obj.len() > MAX_FIELDS {
        set_error("payload has too many fields");
        return Err(ERR_PAYLOAD_INVALID);
    }

    const REQUIRED: [&str; 9] = [
        "v",
        "kid",
        "iat",
        "exp",
        "click_id",
        "offer_id",
        "campaign_id",
        "publisher_id",
        "advertiser_id",
    ];
    for f in REQUIRED {
        if !obj.contains_key(f) {
            set_error(format!("missing required field: {f}"));
            return Err(ERR_PAYLOAD_INVALID);
        }
    }

    if strict_payload_enabled() {
        const ALLOWED: [&str; 18] = [
            "v",
            "kid",
            "iat",
            "exp",
            "click_id",
            "offer_id",
            "campaign_id",
            "publisher_id",
            "advertiser_id",
            "source",
            "sub_id",
            "fbclid",
            "fbp",
            "gclid",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_content",
        ];
        if let Some(unknown) = obj
            .keys()
            .find(|k| !ALLOWED.contains(&k.as_str()) && k.as_str() != "utm_term")
        {
            set_error(format!("unsupported payload field: {unknown}"));
            return Err(ERR_PAYLOAD_INVALID);
        }
    }

    let v = obj.get("v").and_then(Value::as_u64).ok_or_else(|| {
        set_error("v must be an integer");
        ERR_PAYLOAD_INVALID
    })?;
    if v != VERSION as u64 {
        set_error("unsupported payload version");
        return Err(ERR_PAYLOAD_INVALID);
    }

    let kid = obj.get("kid").and_then(Value::as_u64).ok_or_else(|| {
        set_error("kid must be an integer");
        ERR_PAYLOAD_INVALID
    })?;
    if kid == 0 || kid > 255 {
        set_error("kid out of range");
        return Err(ERR_PAYLOAD_INVALID);
    }

    validate_string_field(obj, "click_id", 1, 128)?;

    for name in [
        "source",
        "sub_id",
        "fbclid",
        "fbp",
        "gclid",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
    ] {
        if obj.contains_key(name) {
            validate_string_field(obj, name, 1, 256)?;
        }
    }

    for name in ["offer_id", "campaign_id", "publisher_id", "advertiser_id"] {
        if !obj.get(name).map(Value::is_u64).unwrap_or(false) {
            set_error(format!("{name} must be a non-negative integer"));
            return Err(ERR_PAYLOAD_INVALID);
        }
    }

    let iat = obj.get("iat").and_then(Value::as_i64).ok_or_else(|| {
        set_error("iat must be an integer");
        ERR_PAYLOAD_INVALID
    })?;
    let exp = obj.get("exp").and_then(Value::as_i64).ok_or_else(|| {
        set_error("exp must be an integer");
        ERR_PAYLOAD_INVALID
    })?;

    if exp <= iat {
        set_error("exp must be after iat");
        return Err(ERR_PAYLOAD_INVALID);
    }
    if iat > now.saturating_add(MAX_FUTURE_SKEW) {
        set_error("token iat is too far in the future");
        return Err(ERR_FUTURE_TIME);
    }
    if iat < now.saturating_sub(MAX_TOKEN_AGE_SECONDS) {
        set_error("token iat is too old");
        return Err(ERR_EXPIRED);
    }
    if exp.saturating_sub(iat) > MAX_TTL_SECONDS as i64 {
        set_error("token ttl exceeds maximum");
        return Err(ERR_PAYLOAD_INVALID);
    }
    if check_expiration && now >= exp {
        set_error("tracking token expired");
        return Err(ERR_EXPIRED);
    }

    Ok(())
}

fn aad_for(version: u8, kid: u8) -> [u8; 23] {
    // "ad-tracking-engine:v1" is 21 bytes; append protocol version and KID.
    let mut aad = [0u8; 23];
    aad[..AAD_PREFIX.len()].copy_from_slice(AAD_PREFIX);
    aad[AAD_PREFIX.len()] = version;
    aad[AAD_PREFIX.len() + 1] = kid;
    aad
}

fn encode_payload(mut payload: Value, ttl: u64) -> Result<String, i32> {
    if ttl == 0 || ttl > MAX_TTL_SECONDS {
        set_error("ttl_seconds out of allowed range");
        return Err(ERR_INVALID_ARGUMENT);
    }

    let now = now_unix()?;
    let (kid, key) = current_key()?;
    let key = SecretKey(key);
    let exp = now
        .checked_add(i64::try_from(ttl).map_err(|_| ERR_INVALID_ARGUMENT)?)
        .ok_or_else(|| {
            set_error("ttl calculation overflow");
            ERR_INVALID_ARGUMENT
        })?;

    let obj = payload.as_object_mut().ok_or_else(|| {
        set_error("payload must be a JSON object");
        ERR_PAYLOAD_INVALID
    })?;

    if obj.len() > MAX_FIELDS {
        set_error("payload has too many fields");
        return Err(ERR_PAYLOAD_INVALID);
    }

    obj.insert("v".into(), Value::from(VERSION));
    obj.insert("kid".into(), Value::from(kid));
    obj.insert("iat".into(), Value::from(now));
    obj.insert("exp".into(), Value::from(exp));
    if !obj.contains_key("click_id")
        || obj["click_id"]
            .as_str()
            .map(|s| s.is_empty())
            .unwrap_or(true)
    {
        obj.insert("click_id".into(), Value::String(Uuid::now_v7().to_string()));
    }

    validate_payload(&payload, now, false)?;
    let plaintext = serde_json::to_vec(&payload).map_err(|_| {
        set_error("failed to serialize payload");
        ERR_PAYLOAD_INVALID
    })?;
    if plaintext.len() > MAX_PAYLOAD_CHARS {
        set_error("serialized payload too large");
        return Err(ERR_PAYLOAD_INVALID);
    }

    let mut nonce_bytes = [0u8; NONCE_LEN];
    // AES-GCM nonce 直接使用操作系统 CSPRNG；随机源异常时立即失败，禁止降级。
    if SysRng.try_fill_bytes(&mut nonce_bytes).is_err() {
        set_error("system cryptographic random source failed");
        return Err(ERR_INTERNAL);
    }
    let cipher = Aes256Gcm::new_from_slice(&key.0).map_err(|_| {
        set_error("failed to initialize AES-256-GCM");
        ERR_INTERNAL
    })?;
    let aad = aad_for(VERSION, kid);
    let ciphertext = cipher
        .encrypt(
            Nonce::from_slice(&nonce_bytes),
            Payload {
                msg: &plaintext,
                aad: &aad,
            },
        )
        .map_err(|_| {
            set_error("AES-GCM encryption failed");
            ERR_INTERNAL
        })?;

    let mut raw = Vec::with_capacity(HEADER_LEN + ciphertext.len());
    raw.push(VERSION);
    raw.push(kid);
    raw.extend_from_slice(&nonce_bytes);
    raw.extend_from_slice(&ciphertext);
    Ok(URL_SAFE_NO_PAD.encode(raw))
}

fn decode_token(token: &str, check_expiration: bool) -> Result<Value, i32> {
    if token.is_empty() {
        set_error("token is empty");
        return Err(ERR_TOKEN_INVALID);
    }
    if token.len() > MAX_TOKEN_CHARS {
        set_error("token too large");
        return Err(ERR_TOKEN_TOO_LARGE);
    }

    let raw = URL_SAFE_NO_PAD.decode(token).map_err(|_| {
        set_error("token is not strict Base64URL without padding");
        ERR_TOKEN_INVALID
    })?;
    if raw.len() < MIN_TOKEN_BYTES {
        set_error("token binary length invalid");
        return Err(ERR_TOKEN_INVALID);
    }

    let version = raw[0];
    let kid = raw[1];
    if version != VERSION || kid == 0 {
        set_error("unsupported token version or invalid KID");
        return Err(ERR_TOKEN_INVALID);
    }

    let key = SecretKey(match key_for(kid) {
        Ok(k) => k,
        Err(ERR_KEY_NOT_FOUND) => {
            // Do not expose key-version existence to untrusted callers.
            set_error("token authentication failed");
            return Err(ERR_TOKEN_INVALID);
        }
        Err(e) => return Err(e),
    });
    let cipher = Aes256Gcm::new_from_slice(&key.0).map_err(|_| {
        set_error("failed to initialize AES-256-GCM");
        ERR_INTERNAL
    })?;

    let nonce = &raw[2..HEADER_LEN];
    let ciphertext = &raw[HEADER_LEN..];
    let aad = aad_for(version, kid);
    let plaintext = cipher
        .decrypt(
            Nonce::from_slice(nonce),
            Payload {
                msg: ciphertext,
                aad: &aad,
            },
        )
        .map_err(|_| {
            set_error("token authentication failed");
            ERR_TOKEN_INVALID
        })?;

    if plaintext.len() > MAX_PAYLOAD_CHARS {
        set_error("decrypted payload too large");
        return Err(ERR_PAYLOAD_INVALID);
    }
    let payload: Value = serde_json::from_slice(&plaintext).map_err(|_| {
        set_error("decrypted payload is not valid JSON");
        ERR_PAYLOAD_INVALID
    })?;
    let now = now_unix()?;
    validate_payload(&payload, now, check_expiration)?;

    let obj = payload.as_object().ok_or_else(|| {
        set_error("payload is not an object");
        ERR_PAYLOAD_INVALID
    })?;
    let payload_v = obj.get("v").and_then(Value::as_u64).unwrap_or(0);
    let payload_kid = obj.get("kid").and_then(Value::as_u64).unwrap_or(256);
    if payload_v != VERSION as u64 || payload_kid != kid as u64 {
        set_error("outer header and payload metadata mismatch");
        return Err(ERR_TOKEN_INVALID);
    }
    Ok(payload)
}

fn ffi_guard<F: FnOnce() -> i32 + std::panic::UnwindSafe>(f: F) -> i32 {
    clear_error();
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(rc) => rc,
        Err(_) => {
            set_error("internal panic caught at FFI boundary");
            ERR_INTERNAL
        }
    }
}

fn detect_performance_profile() -> PerformanceProfile {
    let cpu_count = thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1)
        .max(1);
    let recommended_workers = env_usize("AD_TRACKING_WORKERS")
        .filter(|v| *v > 0)
        .unwrap_or_else(|| cpu_count.min(16).max(1));
    let recommended_batch = env_usize("AD_TRACKING_BATCH_SIZE")
        .filter(|v| *v > 0)
        .unwrap_or_else(|| {
            if cpu_count <= 2 {
                2_000
            } else if cpu_count <= 4 {
                5_000
            } else if cpu_count <= 8 {
                10_000
            } else {
                20_000
            }
        });
    PerformanceProfile {
        cpu_count,
        recommended_workers,
        recommended_batch,
    }
}

fn env_usize(name: &str) -> Option<usize> {
    env::var(name).ok()?.trim().parse::<usize>().ok()
}

#[no_mangle]
pub extern "C" fn tracking_set_key(kid: c_uchar, key_hex: *const c_char) -> i32 {
    ffi_guard(|| {
        if kid == 0 {
            set_error("kid=0 is reserved");
            return ERR_INVALID_ARGUMENT;
        }
        let s = match cstr_string(key_hex, 64, "key") {
            Ok(v) => v,
            Err(e) => return e,
        };
        let key = match decode_hex_key(&s) {
            Ok(v) => v,
            Err(e) => return e,
        };
        let mut state = match STATE.write() {
            Ok(v) => v,
            Err(_) => {
                set_error("state lock poisoned");
                return ERR_INTERNAL;
            }
        };
        if !state.keys.contains_key(&kid) && state.keys.len() >= MAX_KEYS {
            set_error("maximum number of keys reached");
            return ERR_INVALID_ARGUMENT;
        }
        if let Some(mut old_key) = state.keys.insert(kid, key) {
            secure_zeroize(&mut old_key);
        }
        OK
    })
}

#[no_mangle]
pub extern "C" fn tracking_remove_key(kid: c_uchar) -> i32 {
    ffi_guard(|| {
        let mut state = match STATE.write() {
            Ok(v) => v,
            Err(_) => {
                set_error("state lock poisoned");
                return ERR_INTERNAL;
            }
        };
        if state.current_kid == Some(kid) {
            set_error("cannot remove current KID");
            return ERR_INVALID_ARGUMENT;
        }
        if let Some(mut old_key) = state.keys.remove(&kid) {
            secure_zeroize(&mut old_key);
            OK
        } else {
            set_error("KID not found");
            ERR_KEY_NOT_FOUND
        }
    })
}

#[no_mangle]
pub extern "C" fn tracking_set_current_kid(kid: c_uchar) -> i32 {
    ffi_guard(|| {
        if kid == 0 {
            set_error("kid=0 is reserved");
            return ERR_INVALID_ARGUMENT;
        }
        let mut state = match STATE.write() {
            Ok(v) => v,
            Err(_) => {
                set_error("state lock poisoned");
                return ERR_INTERNAL;
            }
        };
        if !state.keys.contains_key(&kid) {
            set_error("cannot select a KID that has no key");
            return ERR_KEY_NOT_FOUND;
        }
        state.current_kid = Some(kid);
        OK
    })
}

#[no_mangle]
pub extern "C" fn tracking_generate(
    payload_json: *const c_char,
    ttl_seconds: u64,
    out_token: *mut *mut c_char,
) -> i32 {
    ffi_guard(|| {
        if out_token.is_null() {
            set_error("out_token is null");
            return ERR_NULL_INPUT;
        }
        unsafe {
            *out_token = std::ptr::null_mut();
        }
        let s = match cstr_string(payload_json, MAX_PAYLOAD_CHARS, "payload") {
            Ok(v) => v,
            Err(e) => return e,
        };
        let payload: Value = match serde_json::from_str(&s) {
            Ok(v) => v,
            Err(_) => {
                set_error("payload_json is invalid JSON");
                return ERR_PAYLOAD_INVALID;
            }
        };
        match encode_payload(payload, ttl_seconds) {
            Ok(token) => write_out(out_token, token).map_or_else(|e| e, |_| OK),
            Err(e) => e,
        }
    })
}

#[no_mangle]
pub extern "C" fn tracking_verify(token: *const c_char, out_json: *mut *mut c_char) -> i32 {
    ffi_guard(|| {
        if out_json.is_null() {
            set_error("out_json is null");
            return ERR_NULL_INPUT;
        }
        unsafe {
            *out_json = std::ptr::null_mut();
        }
        let token = match cstr_string(token, MAX_TOKEN_CHARS, "token") {
            Ok(v) => v,
            Err(e) => return e,
        };
        let payload = match decode_token(&token, true) {
            Ok(v) => v,
            Err(e) => return e,
        };
        let json = match serde_json::to_string(&payload) {
            Ok(v) => v,
            Err(_) => {
                set_error("failed to serialize verification result");
                return ERR_INTERNAL;
            }
        };
        write_out(out_json, json).map_or_else(|e| e, |_| OK)
    })
}

#[no_mangle]
pub extern "C" fn tracking_parse(token: *const c_char, out_json: *mut *mut c_char) -> i32 {
    tracking_verify(token, out_json)
}

#[no_mangle]
pub extern "C" fn tracking_free(ptr: *mut c_char) {
    let _ = catch_unwind(AssertUnwindSafe(|| {
        if ptr.is_null() {
            return;
        }
        // SAFETY: pointer must originate from CString::into_raw in this library.
        unsafe {
            drop(CString::from_raw(ptr));
        }
    }));
}

#[no_mangle]
pub extern "C" fn tracking_last_error() -> *const c_char {
    LAST_ERROR.with(|e| {
        e.borrow()
            .as_ref()
            .map(|s| s.as_ptr())
            .unwrap_or(std::ptr::null())
    })
}

#[no_mangle]
pub extern "C" fn tracking_version() -> *const c_char {
    static VERSION_STR: &[u8] = b"ad_tracking_engine/1.1.0\0";
    VERSION_STR.as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn tracking_abi_version() -> u32 {
    ABI_VERSION
}

#[no_mangle]
pub extern "C" fn tracking_performance_info(out_json: *mut *mut c_char) -> i32 {
    ffi_guard(|| {
        if out_json.is_null() {
            set_error("out_json is null");
            return ERR_NULL_INPUT;
        }
        unsafe {
            *out_json = std::ptr::null_mut();
        }
        let p = *PERFORMANCE;
        let value = serde_json::json!({
            "cpu_count": p.cpu_count,
            "recommended_workers": p.recommended_workers,
            "recommended_batch_size": p.recommended_batch,
            "security_limits_fixed": true,
            "workers_env": "AD_TRACKING_WORKERS",
            "batch_env": "AD_TRACKING_BATCH_SIZE",
        });
        match serde_json::to_string(&value) {
            Ok(s) => write_out(out_json, s).map_or_else(|e| e, |_| OK),
            Err(_) => {
                set_error("failed to serialize performance info");
                ERR_INTERNAL
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Once;
    static INIT: Once = Once::new();

    fn init() {
        INIT.call_once(|| {
            let mut state = STATE.write().unwrap();
            state.keys.clear();
            state.keys.insert(1, [0x11; 32]);
            state.keys.insert(2, [0x22; 32]);
            state.current_kid = Some(1);
        });
    }

    fn token() -> String {
        init();
        encode_payload(serde_json::json!({
            "offer_id": 10001, "campaign_id": 20001, "publisher_id": 30001, "advertiser_id": 40001,
            "source": "facebook"
        }), 3600).unwrap()
    }

    #[test]
    fn round_trip() {
        let t = token();
        let p = decode_token(&t, true).unwrap();
        assert_eq!(p["v"], 1);
        assert_eq!(p["kid"], 1);
        assert!(p["click_id"].as_str().unwrap().len() > 10);
    }

    #[test]
    fn tampering_is_rejected() {
        for index in [HEADER_LEN + 2, 2, usize::MAX] {
            let t = token();
            let mut bytes = URL_SAFE_NO_PAD.decode(&t).unwrap();
            let idx = if index == usize::MAX {
                bytes.len() - 1
            } else {
                index
            };
            bytes[idx] ^= 1;
            let bad = URL_SAFE_NO_PAD.encode(bytes);
            assert_eq!(decode_token(&bad, true).unwrap_err(), ERR_TOKEN_INVALID);
        }
    }

    #[test]
    fn truncation_is_rejected() {
        let t = token();
        assert!(decode_token(&t[..t.len() / 2], true).is_err());
    }

    #[test]
    fn strict_schema_rejects_unknown_fields() {
        let payload = serde_json::json!({
            "offer_id": 10001, "campaign_id": 20001, "publisher_id": 30001, "advertiser_id": 40001,
            "unknown_field": "x"
        });
        assert_eq!(
            encode_payload(payload, 3600).unwrap_err(),
            ERR_PAYLOAD_INVALID
        );
    }

    #[test]
    fn performance_profile_is_sane() {
        assert!(PERFORMANCE.cpu_count >= 1);
        assert!(PERFORMANCE.recommended_workers >= 1);
        assert!(PERFORMANCE.recommended_batch >= 1);
    }

    #[test]
    fn header_payload_binding_is_enforced() {
        let t = token();
        let mut bytes = URL_SAFE_NO_PAD.decode(&t).unwrap();
        bytes[1] = 2;
        let bad = URL_SAFE_NO_PAD.encode(bytes);
        assert!(decode_token(&bad, true).is_err());
    }
}
