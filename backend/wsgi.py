"""WSGI entry point: gunicorn wsgi:app (Railway, Docker, Elastic Beanstalk Procfile)."""
from app import create_app

app = create_app()
application = app  # Elastic Beanstalk's default WSGIPath name, in case no Procfile is used
