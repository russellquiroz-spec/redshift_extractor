"""
redshift_extractor: librería interna para extraer data desde Amazon Redshift vía túnel SSH.

API pública:
- list_databases() -> List[str]
- extract_sql(db: str, query: str) -> pandas.DataFrame
"""
from redshift_extractor.extractor import extract_sql, list_databases
__all__ = ["extract_sql", "list_databases"]
__version__ = "0.1.0"