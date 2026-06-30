import psutil
import os
import glob
import inspect

print("=== psutil version ===")
print(psutil.__version__)

print("\n=== .pyi stubs bundled in psutil package ===")
stubs = glob.glob(os.path.join(os.path.dirname(psutil.__file__), "**", "*.pyi"), recursive=True)
print(f"{len(stubs)} .pyi files found")

print("\n=== py.typed marker ===")
print(f"py.typed: {'PRESENT' if os.path.exists(os.path.join(os.path.dirname(psutil.__file__), 'py.typed')) else 'NOT FOUND'}")

print("\n=== psutil-stubs as separate pkg ===")
try:
    import psutil_stubs
    print(f"psutil-stubs installed at {os.path.dirname(psutil_stubs.__file__)}")
except ImportError:
    print("NOT installed")

print("\n=== Process methods matching 'io' or 'count' ===")
for attr in dir(psutil.Process):
    if 'io' in attr.lower() or 'count' in attr.lower():
        print(f"  {attr}")

print("\n=== psutil.Process.io_counters check ===")
print(f"Has attr on class: {hasattr(psutil.Process, 'io_counters')}")

print("\n=== psutil.disk_io_counters ===")
print(f"Function exists: {hasattr(psutil, 'disk_io_counters')}")
print(f"Type: {type(psutil.disk_io_counters)}")
try:
    sig = inspect.signature(psutil.disk_io_counters)
    print(f"Signature: {sig}")
    hints = getattr(psutil.disk_io_counters, '__annotations__', None)
    print(f"__annotations__: {hints}")
except Exception as e:
    print(f"Signature error: {e}")
