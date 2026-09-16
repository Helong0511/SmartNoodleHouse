use ad_tracking_engine::{tracking_generate, tracking_set_current_kid, tracking_set_key, tracking_verify, tracking_free};
use criterion::{criterion_group, criterion_main, Criterion};
use std::ffi::CString;

fn setup() {
    let key = CString::new("11".repeat(32)).unwrap();
    assert_eq!(tracking_set_key(1, key.as_ptr()), 0);
    assert_eq!(tracking_set_current_kid(1), 0);
}

fn benchmark_generate(c: &mut Criterion) {
    setup();
    let payload = CString::new(r#"{"offer_id":10001,"campaign_id":20001,"publisher_id":30001,"advertiser_id":40001,"source":"facebook"}"#).unwrap();
    c.bench_function("tracking_generate", |b| {
        b.iter(|| {
            let mut out = std::ptr::null_mut();
            let rc = tracking_generate(payload.as_ptr(), 3600, &mut out);
            assert_eq!(rc, 0);
            tracking_free(out);
        })
    });
}

fn benchmark_verify(c: &mut Criterion) {
    setup();
    let payload = CString::new(r#"{"offer_id":10001,"campaign_id":20001,"publisher_id":30001,"advertiser_id":40001,"source":"facebook"}"#).unwrap();
    let mut token = std::ptr::null_mut();
    assert_eq!(tracking_generate(payload.as_ptr(), 3600, &mut token), 0);
    let token_owned = unsafe { std::ffi::CStr::from_ptr(token).to_owned() };
    tracking_free(token);
    c.bench_function("tracking_verify", |b| {
        b.iter(|| {
            let mut out = std::ptr::null_mut();
            let rc = tracking_verify(token_owned.as_ptr(), &mut out);
            assert_eq!(rc, 0);
            tracking_free(out);
        })
    });
}

criterion_group!(benches, benchmark_generate, benchmark_verify);
criterion_main!(benches);
