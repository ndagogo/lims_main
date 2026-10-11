"""Page the staff lists. Each list on a screen uses its own query parameter."""
from django.core.paginator import Paginator

PAGE_SIZES = (5, 10, 25, 50)


def page_size(request):
    try:
        size = int(request.GET.get('per') or 10)
    except (TypeError, ValueError):
        size = 10
    return size if size in PAGE_SIZES else 10


def paginate(request, queryset, param='page'):
    paginator = Paginator(queryset, page_size(request))
    page = paginator.get_page(request.GET.get(param) or 1)
    query = request.GET.copy()
    query.pop(param, None)
    page.param = param
    page.base_query = query.urlencode()
    return page
