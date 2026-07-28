// Prevents an extra console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if ai4s_workbench_lib::is_skill_mcp_invocation(std::env::args_os().skip(1)) {
        std::process::exit(ai4s_workbench_lib::run_skill_mcp_stdio());
    }
    ai4s_workbench_lib::run()
}
