# Thin shim. The real runner is run.py, because the Windows build machine has no make.
# Every target here is `python run.py <same-name>`.
PY := .venv/Scripts/python.exe
.PHONY: test-fast test gate gate-clean demo data size
test-fast: ; @$(PY) run.py test-fast
test:      ; @$(PY) run.py test
gate:      ; @$(PY) run.py gate
gate-clean:; @$(PY) run.py gate-clean
demo:      ; @$(PY) run.py demo
data:      ; @$(PY) run.py data
size:      ; @$(PY) run.py size
