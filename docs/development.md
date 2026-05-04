# Development

## Build docs locally

```bash
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

## Live reload

```bash
pip install sphinx-autobuild
sphinx-autobuild docs docs/_build/html
```

## Mocked embedded imports

The docs config mocks these modules so desktop builds do not fail:

`bluetooth, framebuf, machine, micropython, network, uasyncio, ubinascii`
