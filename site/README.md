# Buy or Wait? Decision Explorer

This is a static explorer over precomputed run data. It does not run the Python engine in
the browser and it does not make model calls.

Regenerate the bundle after changing `output.csv`:

```bash
python code/scripts/build_static_explorer.py --output-dir site
```

Serve the repository root:

```bash
python -m http.server 8765
```

Then open `http://127.0.0.1:8765/site/`.
