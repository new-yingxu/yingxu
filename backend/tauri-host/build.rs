fn main() {
    println!("cargo:rerun-if-env-changed=YINGXU_CLOUDBASE_AUTH_ORIGIN");
    println!("cargo:rerun-if-env-changed=YINGXU_CLOUDBASE_CLIENT_ID");
    tauri_build::build()
}
