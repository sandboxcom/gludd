# android_diagnose

Gather Android diagnostic data via ADB: logcat system logs, dumpsys service
state, getprop build properties, and pm list package inventory. Requires
ADB access to the target device (set `adb_serial` for multi-device).
