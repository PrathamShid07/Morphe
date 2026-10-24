# Django URL routing and views

## URL configuration

Django maps incoming request paths to views through URLconf modules. The ROOT_URLCONF setting names the root urls.py, and every URLconf exposes a list called urlpatterns. Django checks the patterns in order and stops at the first match.

## Path converters

The path() function supports converters such as int, str, slug, uuid and path. A pattern like path("articles/<int:year>/", views.year_archive) passes year to the view as an integer. Use re_path() when a regular expression is required.

## Including other URLconfs

The include() function lets an app own its URLs, so project urls.py only delegates with path("blog/", include("blog.urls")). Setting app_name creates a namespace, and reverse("blog:detail", args=[3]) builds the URL without hard coding it.

## Function and class based views

A view is a callable that takes an HttpRequest and returns an HttpResponse. Class based views subclass View, TemplateView or ListView and are routed with as_view(). Mixins such as LoginRequiredMixin add behaviour without rewriting the view.

## Middleware

Middleware classes wrap every request and response in the order listed in the MIDDLEWARE setting. SessionMiddleware, CsrfViewMiddleware and AuthenticationMiddleware are enabled by default in new projects. A custom middleware implements __call__ and may short circuit the request.
