fn main() {
    println!("cargo:rerun-if-env-changed=YINGXU_CLOUDBASE_AUTH_ORIGIN");
    println!("cargo:rerun-if-env-changed=YINGXU_CLOUDBASE_CLIENT_ID");
    println!("cargo:rerun-if-env-changed=YINGXU_CLOUD_API_ORIGIN");
    tauri_build::build()
}
