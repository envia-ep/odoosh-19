from unittest.mock import MagicMock, patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.services.envia_geocodes_client import EnviaGeocodesClient

_MX_GEOCODE = [
    {
        "locality": "Ciudad de México",
        "state": {
            "iso_code": "MX-CMX",
            "code": {"2digit": "CMX"},
        },
    }
]


@tagged("post_install", "-at_install")
class TestEnviaGeocodesClient(TransactionCase):
    @patch("odoo.addons.envia.services.envia_geocodes_client.requests.get")
    def test_lookup_zipcode_parses_mx_result(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: _MX_GEOCODE)
        entries = EnviaGeocodesClient().lookup_zipcode("MX", "03100")
        self.assertEqual(entries[0].get("locality"), "Ciudad de México")
        mock_get.assert_called_once()

    @patch("odoo.addons.envia.services.envia_geocodes_client.requests.get")
    def test_wizard_resolves_state_from_geocode(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: _MX_GEOCODE)
        mexico = self.env.ref("base.mx")
        expected_state = self.env.ref("base.state_mx_df")
        wizard = self.env["envia.quote.wizard"].create(
            {
                "destination_location_type": "address",
                "destination_country_id": mexico.id,
                "destination_postal_code": "03100",
            }
        )
        wizard._apply_geocode("destination", force=True)
        self.assertEqual(wizard.destination_city, "Ciudad de México")
        self.assertEqual(wizard.destination_state_id, expected_state)

    @patch("odoo.addons.envia.services.envia_geocodes_client.requests.get")
    def test_resolve_state_from_postal_code_maps_mx_cmx(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: _MX_GEOCODE)
        mexico = self.env.ref("base.mx")
        expected_state = self.env.ref("base.state_mx_df")
        state = EnviaGeocodesClient().resolve_state_from_postal_code(
            self.env,
            mexico,
            "06500",
        )
        self.assertEqual(state, expected_state)
