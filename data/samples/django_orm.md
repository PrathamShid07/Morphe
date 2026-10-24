# Django ORM and database configuration

## Defining models

A Django model is a class that subclasses models.Model, and each attribute such as CharField or ForeignKey becomes a database column. Running makemigrations records the change and migrate applies it to the database.

## Configuring the database

The DATABASES setting selects the backend. For PostgreSQL use ENGINE django.db.backends.postgresql with NAME, USER, PASSWORD, HOST and PORT 5432. Set CONN_MAX_AGE to 60 to reuse connections for sixty seconds instead of reconnecting on every request.

## QuerySets

QuerySets are lazy: no SQL runs until the QuerySet is evaluated by iteration, slicing or list(). Chain filter(), exclude() and order_by() to build a query, and use values() to fetch dictionaries instead of model instances.

## Avoiding N+1 queries

Use select_related() for ForeignKey and OneToOne joins and prefetch_related() for many to many relations. Without them a loop over related objects issues one extra query per row, which the Django Debug Toolbar makes visible.

## Transactions

Wrap related writes in transaction.atomic() so they commit or roll back together. Setting ATOMIC_REQUESTS to True runs every view inside a transaction, and select_for_update() locks rows until the transaction ends.
