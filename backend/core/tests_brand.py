from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from core.middleware import ProductBrandMiddleware


class ProductBrandMiddlewareTests(SimpleTestCase):
    def test_html_pages_use_the_product_name(self):
        html = (
            '<title>Django administration</title>'
            '<a href="https://www.django-rest-framework.org/">Django REST framework</a>'
            '<label>Django admin access</label>'
        )

        def view(request):
            return HttpResponse(html, content_type='text/html; charset=utf-8')

        response = ProductBrandMiddleware(view)(RequestFactory().get('/'))
        self.assertNotContains(response, 'Django')
        self.assertContains(response, 'AuxaCare Access')
        self.assertNotContains(response, 'django-rest-framework.org')
