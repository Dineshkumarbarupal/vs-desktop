"""Sample Python program."""
from datetime import datetime

def greet(name: str = "developer") -> str:
    return f"Hello, {name}!"

if __name__ == "__main__":
    print(greet("FlowCode Studio"))
    print(f"Run at {datetime.now():%H:%M:%S}")
