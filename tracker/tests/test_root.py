from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db.utils import OperationalError
from rest_framework import status
from rest_framework.test import APITestCase


class HealthTests(APITestCase):
    def test_reports_status_and_version(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["version"], "0.1.0")
        self.assertEqual(body["database"]["status"], "ok")
        self.assertTrue(body["docs"].endswith("/api/docs/"))

    def test_database_down_returns_503(self):
        with mock.patch("config.views.connection.ensure_connection", side_effect=OperationalError):
            response = self.client.get("/")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.json()["status"], "degraded")


class ApiDocsTests(APITestCase):
    def test_api_root_redirects_to_swagger(self):
        response = self.client.get("/api/")
        self.assertRedirects(response, "/api/docs/", fetch_redirect_response=False)

    def test_swagger_and_redoc_pages_load(self):
        for url in ["/api/docs/", "/api/redoc/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

    def test_schema_lists_every_route_with_its_methods(self):
        schema = self.client.get("/api/schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json").json()
        http_methods = {"get", "post", "put", "patch", "delete"}
        routes = {path: sorted(set(ops) & http_methods) for path, ops in schema["paths"].items()}
        self.assertEqual(
            routes,
            {
                "/": ["get"],
                "/api/concepts/": ["get", "post"],
                "/api/concepts/{id}/": ["get", "patch", "put"],
                "/api/entries/": ["get"],
                "/api/entries/from-text/": ["post"],
                "/api/entries/{id}/": ["delete", "get"],
                "/api/entries/{id}/answer/": ["post"],
            },
        )
        # Custom actions document the entry as response, not their input serializer.
        created = schema["paths"]["/api/entries/from-text/"]["post"]["responses"]["201"]
        self.assertTrue(created["content"]["application/json"]["schema"]["$ref"].endswith("/Entry"))

    def test_schema_is_valid_and_has_no_warnings(self):
        call_command("spectacular", "--validate", "--fail-on-warn", stdout=StringIO())
