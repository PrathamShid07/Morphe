# Python web serving notes

## WSGI servers

WSGI is the synchronous interface between Python web applications and servers. Gunicorn runs Flask and Django applications with several worker processes, and the number of workers is commonly set to two times the CPU cores plus one.

## ASGI servers

ASGI extends WSGI with asynchronous handlers, websockets and long lived connections. Uvicorn and Hypercorn serve ASGI applications, and a single event loop can handle many concurrent connections without a thread per request.

## Flask routing

Flask maps URLs to view functions with the @app.route decorator, groups related routes into blueprints, and keeps request data in the request object. The development server must not be used in production.

## Sessions and cookies

Web sessions store signed cookies or a server side session id. Set the Secure, HttpOnly and SameSite attributes on session cookies to reduce theft and cross site request forgery.

## Template rendering

Jinja2 renders HTML from templates with variables, loops, filters and template inheritance. Autoescaping must stay enabled so user input cannot inject markup.

## Static files

Serve static assets such as CSS and images from a CDN or the web server rather than the Python process, and fingerprint file names so browsers can cache them for a long time.
