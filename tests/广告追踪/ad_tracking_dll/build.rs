fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=windows/version.rc");

    #[cfg(target_os = "windows")]
    {
        // 使用 winres 写入 PE Version Resource。
        // 联系方式属于产品元数据，不写入 FileDescription/Comments，避免重复暴露。
        let mut res = winres::WindowsResource::new();
        res.set("CompanyName", "深圳市指动空间科技有限公司");
        res.set("FileDescription", "广告追踪安全引擎");
        res.set("FileVersion", "1.1.0.0");
        res.set("InternalName", "ad_tracking_engine");
        res.set("OriginalFilename", "ad_tracking_engine.dll");
        res.set("ProductName", "Ad Tracking Engine");
        res.set("ProductVersion", "1.1.0.0");
        res.set(
            "LegalCopyright",
            "版权所有 © 2026 深圳市指动空间科技有限公司",
        );
        res.set("Comments", "Rust 高性能广告追踪安全引擎");
        res.set("Contact", "18011189359");
        res.compile()
            .expect("无法编译 Windows DLL Version Resource");
    }
}
