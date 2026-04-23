from dotenv import load_dotenv
import os
from neo4j import GraphDatabase

load_dotenv()

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

with driver.session() as session:
    result = session.run("RETURN 'Connected!' AS msg")
    print(result.single()["msg"])

driver.close()