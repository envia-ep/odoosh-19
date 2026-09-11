import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.envia_client import EnviaClient


class EnviaWarehouseOriginWizard(models.TransientModel):
    _name = "envia.warehouse.origin.wizard"
    _description = "Link Warehouse to Envia Origin Address"

    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Warehouse",
        check_company=True,
        domain="[('company_id', '=', company_id)]",
    )
    warehouse_readonly = fields.Boolean()
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    address_line_ids = fields.One2many(
        "envia.warehouse.origin.wizard.address",
        "wizard_id",
        string="Available origins",
    )
    address_line_id = fields.Many2one(
        "envia.warehouse.origin.wizard.address",
        string="Envia origin address",
        domain="[('wizard_id', '=', id)]",
    )
    update_warehouse_partner = fields.Boolean(
        string="Update warehouse contact address",
        default=True,
        help="Write street, city, zip and contact details onto the warehouse partner.",
    )
    load_error = fields.Char(readonly=True)

    @api.model
    def _fetch_origin_addresses(self, company=None):
        company = company or self.env.company
        shop_id = (company.envia_shop_id or "").strip()
        token = (company.envia_api_token or "").strip()
        if not shop_id:
            raise UserError(_("Connect Envia first: shop id is missing on this company."))
        if not token:
            raise UserError(
                _("Configure the Envia API token in Settings > Envia Shipping.")
            )
        base_url = company._envia_get_queries_base_url()
        client = EnviaClient(base_url, token)
        return client.get_shop_default_addresses(shop_id)

    def _set_address_lines(self, addresses: list[dict]) -> None:
        self.ensure_one()
        self.address_line_ids.unlink()
        lines = []
        for entry in addresses:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": entry.get("label") or str(entry.get("id")),
                        "envia_address_id": str(entry.get("id")),
                        "payload_json": json.dumps(entry, ensure_ascii=False),
                    },
                )
            )
        self.write(
            {
                "address_line_ids": lines,
                "address_line_id": False,
            }
        )

    @api.model
    def action_open_wizard(self):
        company = self.env.company
        warehouse = self.env["stock.warehouse"]
        if self.env.context.get("default_warehouse_id"):
            warehouse = self.env["stock.warehouse"].browse(
                self.env.context["default_warehouse_id"]
            )
        elif (
            self.env.context.get("active_model") == "stock.warehouse"
            and self.env.context.get("active_id")
        ):
            warehouse = self.env["stock.warehouse"].browse(self.env.context["active_id"])
        if warehouse:
            company = warehouse.company_id or company
        load_error = False
        addresses = []
        try:
            addresses = self._fetch_origin_addresses(company)
        except UserError as error:
            load_error = str(error)
        wizard = self.create(
            {
                "company_id": company.id,
                "warehouse_id": warehouse.id if warehouse else False,
                "warehouse_readonly": bool(warehouse),
                "update_warehouse_partner": True,
                "load_error": load_error or False,
            }
        )
        if addresses:
            wizard._set_address_lines(addresses)
            if warehouse.envia_origin_id:
                current_id = warehouse.envia_origin_id.envia_address_id
                match_line = wizard.address_line_ids.filtered(
                    lambda line: line.envia_address_id == current_id
                )[:1]
                if match_line:
                    wizard.address_line_id = match_line
        return {
            "type": "ir.actions.act_window",
            "name": _("Link warehouse to Envia origin"),
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_refresh_addresses(self):
        self.ensure_one()
        addresses = self._fetch_origin_addresses(self.company_id)
        self.write({"load_error": False})
        self._set_address_lines(addresses)
        if not addresses:
            raise UserError(_("Envia returned no origin addresses for this shop."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Link warehouse to Envia origin"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_save(self):
        self.ensure_one()
        if not self.warehouse_id:
            raise UserError(_("Select a warehouse."))
        if not self.address_line_id:
            raise UserError(_("Select an Envia origin address."))
        try:
            address = json.loads(self.address_line_id.payload_json or "{}")
        except json.JSONDecodeError as error:
            raise UserError(_("Invalid Envia address payload.")) from error
        if not address.get("id"):
            raise UserError(_("Refresh origin addresses, then select an Envia address."))
        self.env["envia.warehouse.origin"].upsert_match(
            self.company_id,
            self.warehouse_id,
            address,
            update_partner=self.update_warehouse_partner,
        )
        return {"type": "ir.actions.act_window_close"}


class EnviaWarehouseOriginWizardAddress(models.TransientModel):
    _name = "envia.warehouse.origin.wizard.address"
    _description = "Envia Origin Address Option"
    _order = "name"

    wizard_id = fields.Many2one(
        "envia.warehouse.origin.wizard",
        required=True,
        ondelete="cascade",
    )
    name = fields.Char(required=True)
    envia_address_id = fields.Char(required=True)
    payload_json = fields.Text(required=True)
