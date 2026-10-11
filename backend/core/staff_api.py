"""Staff HTML forms call the same API actions the mobile app uses."""
from rest_framework.test import APIRequestFactory, force_authenticate

_factory = APIRequestFactory()


def describe(data):
    if data is None:
        return 'Saved.'
    if isinstance(data, (list, tuple)):
        return '; '.join(describe(item) for item in data)
    if isinstance(data, dict):
        if set(data.keys()) == {'detail'}:
            return describe(data['detail'])
        return '; '.join(f'{key}: {describe(value)}' for key, value in data.items())
    return str(data)


def _invoke(viewset, mapping, user, data, pk=None, files=None):
    payload = dict(data or {})
    if files:
        payload.update(files)
        request = _factory.post('/', payload, format='multipart')
    else:
        request = _factory.post('/', payload, format='json')
    force_authenticate(request, user=user)
    response = viewset.as_view(mapping)(request, pk=pk) if pk is not None else viewset.as_view(mapping)(request)
    body = getattr(response, 'data', None)
    return response.status_code, body


def call_create(viewset, user, data, files=None):
    return _invoke(viewset, {'post': 'create'}, user, data, files=files)


def call_action(viewset, action, user, pk, data=None, files=None):
    return _invoke(viewset, {'post': action}, user, data, pk=pk, files=files)
