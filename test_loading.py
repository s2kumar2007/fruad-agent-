import os, requests, pyTigerGraph as tg
from dotenv import load_dotenv

load_dotenv()
host = os.environ.get("TG_HOST")
secret = os.environ.get("TG_SECRET")
graph = os.environ.get("TG_GRAPH")

token_global = requests.post(f"{host}/gsql/v1/tokens", json={"secret": secret}, headers={"Content-Type": "application/json"}).json()["token"]
conn = tg.TigerGraphConnection(host=host, graphname=graph, apiToken=token_global)
conn.apiToken = token_global

text = """USE GRAPH FraudGraph
CREATE LOADING JOB load_fraud_graph FOR GRAPH FraudGraph {
  DEFINE FILENAME f_customers;
  LOAD f_customers TO VERTEX Customer VALUES ($"customer_id") USING header="true", separator=",";
}
"""
print(conn.gsql(text))
