"""Offline review harness; no application/provider process is started."""
import json,os,socket,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
os.chdir(ROOT);sys.path.insert(0,str(ROOT))
attempts=[]
def blocked(*a,**kw):
    attempts.append('blocked')
    raise AssertionError('Review07 forbids network transport')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=blocked
def main():
    import pytest
    args=sys.argv[1:]
    code=pytest.main(['-q','-p','no:cacheprovider',*args])
    Path(os.environ.get('REVIEW07_TEST_SUMMARY',str(ROOT/'reports/review07-remediation/test-summary.json'))).write_text(json.dumps({'exit_code':code,'network_attempts':len(attempts),'args':args},indent=2)+'\n')
    sys.exit(code)

if __name__ == "__main__":
    main()
