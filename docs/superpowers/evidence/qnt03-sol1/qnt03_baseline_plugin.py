from pathlib import Path

def pytest_configure(config):
    import trader.brain.analyst as module
    source = Path(__file__).with_name('analyst.py').read_text()
    exec(compile(source, '45e825d:trader/brain/analyst.py', 'exec'), module.__dict__)
