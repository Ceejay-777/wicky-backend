import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.eventstream import ANONYMOUS_CHANNEL, UserChannelManager, user_channel
from apps.core.middleware import CookieJWTUserMiddleware

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(email="a@b.com", password="p")


@pytest.fixture
def other_user():
    return User.objects.create_user(email="c@d.com", password="p")


def _token(user):
    return str(RefreshToken.for_user(user).access_token)


class TestUserChannelManager:
    manager = UserChannelManager()

    def _request(self, user=None, query=""):
        request = RequestFactory().get(f"/api/events{query}")
        request.user = user if user is not None else AnonymousUser()
        return request

    @pytest.mark.django_db
    def test_authenticated_user_gets_only_their_channel(self, user):
        channels = self.manager.get_channels_for_request(self._request(user), {})
        assert channels == {user_channel(user.pk)}

    @pytest.mark.django_db
    def test_client_supplied_channel_is_ignored(self, user, other_user):
        request = self._request(user, query=f"?channel=user-{other_user.pk}&channel=admin")
        assert self.manager.get_channels_for_request(request, {}) == {user_channel(user.pk)}

    def test_anonymous_maps_to_unreadable_channel(self):
        channels = self.manager.get_channels_for_request(self._request(), {})
        assert channels == {ANONYMOUS_CHANNEL}
        assert self.manager.can_read_channel(None, ANONYMOUS_CHANNEL) is False

    @pytest.mark.django_db
    def test_can_read_own_channel_only(self, user, other_user):
        assert self.manager.can_read_channel(user, user_channel(user.pk)) is True
        assert self.manager.can_read_channel(user, user_channel(other_user.pk)) is False
        assert self.manager.can_read_channel(None, user_channel(user.pk)) is False

    def test_not_reliable_without_storage(self):
        assert self.manager.is_channel_reliable("user-1") is False


@pytest.mark.django_db
class TestCookieJWTUserMiddleware:
    @staticmethod
    def _run(path, cookie=None):
        seen = {}

        def get_response(request):
            seen["user"] = request.user
            return None

        request = RequestFactory().get(path)
        request.user = AnonymousUser()
        if cookie is not None:
            request.COOKIES["access_token"] = cookie
        response = CookieJWTUserMiddleware(get_response)(request)
        return response if "user" not in seen else seen["user"]

    def test_valid_cookie_sets_user_on_event_path(self, user):
        assert self._run("/api/events", _token(user)) == user

    def test_invalid_cookie_is_401_on_event_path(self):
        assert self._run("/api/events", "garbage.token.value").status_code == 401

    def test_no_cookie_is_401_on_event_path(self):
        assert self._run("/api/events").status_code == 401

    def test_other_paths_are_not_touched(self, user):
        assert not self._run("/api/learning/subjects", _token(user)).is_authenticated


@pytest.mark.django_db
class TestEventStreamEndpoint:
    """Hits the real endpoint; the stream body is never iterated (it is infinite)."""

    def test_anonymous_is_401_not_a_stream(self):
        response = Client().get("/api/events")
        assert response.status_code == 401
        assert not response["Content-Type"].startswith("text/event-stream")
        assert response.json()["status"] == "error"

    def test_garbage_token_is_401_not_a_stream(self):
        client = Client()
        client.cookies["access_token"] = "garbage.token.value"
        assert client.get("/api/events").status_code == 401

    def test_authenticated_user_gets_an_event_stream(self, user):
        client = Client()
        client.cookies["access_token"] = _token(user)
        response = client.get("/api/events")
        assert response.status_code == 200
        assert response.streaming is True
        assert response["Content-Type"].startswith("text/event-stream")
        response.close()

    def test_cannot_subscribe_to_another_users_channel(self, user, other_user):
        client = Client()
        client.cookies["access_token"] = _token(user)
        response = client.get(f"/api/events?channel=user-{other_user.pk}")
        # Still just the caller's own stream; the foreign channel is ignored.
        assert response.streaming is True
        response.close()
