# Buy or Wait?

Terminal solution for the HackerRank Orchestrate "Buy or Wait?" challenge.

## Requirements

- Python 3.13, standard library only.
- Optional live model access through `OPENROUTER_API_KEY`.

## Run

If the archive is extracted into a directory named `code/`, place that directory next to the provided `dataset/` directory and run:

```bash
python code/main.py
```

If the archive contents are extracted directly into the current directory instead, run `python main.py`.

The command writes `output.csv` next to `dataset/`. With `OPENROUTER_API_KEY` set, the model-backed evidence layer is used for message extraction, image document extraction, and guarded explanation polish. Without the key, the program degrades to the deterministic offline path:

```bash
python code/main.py --no-llm
```

Useful options:

```bash
python code/main.py --dataset dataset --output output.csv
python code/main.py --samples
python code/main.py --requests request_01,request_02
```

## Verification

Validate a generated submission file:

```bash
python code/evaluation/check_output.py output.csv --rows 250 --validate --no-fallback-rows
```

Run the test suite from the repository root:

```bash
python -m unittest discover -s code/tests -t .
```

## Token Usage

The final full-dataset live run is summarized in `evaluation/usage_report.md`. The report lists the provider, model names, model roles, call counts, input and output tokens, total and per-request token averages, and estimated cost. It contains no API key or prompt text.
