import re
from pathlib import Path

MACRO_FILE = Path(__file__).parent.parent / "transform" / "macros" / "create_functions.sql"


def register_functions(con):
    """Create the SQL functions of the dbt project in a test database, from the real definitions."""
    body = re.search(r"{% macro create_functions\(\) %}(.*){% endmacro %}", MACRO_FILE.read_text(), re.S)[1]
    con.execute(re.sub(r"{#.*?#}", "", body, flags=re.S))
