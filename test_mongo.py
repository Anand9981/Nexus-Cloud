from pymongo import MongoClient   # PyMongo driver import
import certifi                    # SSL certificates handle karne ke liye
import os                         # Environment variables read karne ke liye

client = MongoClient(
    os.getenv("MONGO_URI"),       # .env file se MONGO_URI read karega
    tlsCAFile=certifi.where()     # Atlas ke SSL certificate verify karega
)
print(client.list_database_names())  # Connected cluster ke databases list karega
