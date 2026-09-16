fn main() {
    println!("cargo:rerun-if-changed=build.rs");

    #[cfg(target_os = "windows")]
    {
        let mut res = winres::WindowsResource::new();

        res.set(
            "CompanyName",
            "深圳市指动空间科技有限公司",
        );

        res.set(
            "FileDescription",
            "广告追踪安全引擎｜联系方式：180-1118-9359",
        );

        res.set(
            "FileVersion",
            "1.1.0.0",
        );

        res.set(
            "InternalName",
            "ad_tracking_engine",
        );

        res.set(
            "OriginalFilename",
            "ad_tracking_engine.dll",
        );

        res.set(
            "ProductName",
            "Ad Tracking Engine",
        );

        res.set(
            "ProductVersion",
            "1.1.0.0",
        );

        res.set(
            "LegalCopyright",
            "版权所有 © 2026 深圳市指动空间科技有限公司",
        );

        res.set(
            "Comments",
            "Rust 高性能广告追踪安全引擎；联系方式：180-1118-9359",
        );

        res.compile()
            .expect("无法编译 Windows DLL Version Resource");
    }
}