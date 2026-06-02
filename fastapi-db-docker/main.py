from fastapi import FastAPI, Depends
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import os

# 1. Connect to the Database
# Notice the hostname is 'db' - this is the name we will give our Postgres container!
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://myuser:mypassword@db:5432/fastapidb")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. Define a simple User table
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)

# Create the tables in the database
Base.metadata.create_all(bind=engine)

app = FastAPI()

# Database Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 3. Create an endpoint to INSERT data into Postgres
@app.post("/users/")
def create_user(name: str, db: Session = Depends(get_db)):
    new_user = User(name=name)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User added!", "user": new_user}

# 4. Create an endpoint to READ data from Postgres
@app.get("/users/")
def read_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return {"users": users}
