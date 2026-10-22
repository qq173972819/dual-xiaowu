import os, runpy, sys
os.environ.pop("MSYSTEM", None)
sys.path.insert(0, r"C:\esp\v5.5.5\esp-idf\tools")
sys.argv = ["idf.py"] + sys.argv[1:]
runpy.run_path(r"C:\esp\v5.5.5\esp-idf\tools\idf.py", run_name="__main__")
