from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EnviaWarehouseOrigin(models.Model):
    _name = "envia.warehouse.origin"
    _description = "Warehouse Envia Origin Address"
    _order = "warehouse_id"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Warehouse",
        required=True,
        check_company=True,
        ondelete="cascade",
        index=True,
    )
    envia_address_id = fields.Char(string="Envia Address ID", required=True)
    envia_address_label = fields.Char(string="Envia Origin Address")
    street = fields.Char()
    city = fields.Char()
    zip = fields.Char()
    phone = fields.Char()
    email = fields.Char()
    country_code = fields.Char()
    state_code = fields.Char()

    _warehouse_uniq = models.Constraint(
        "unique(warehouse_id)",
        "This warehouse is already linked to an Envia origin address.",
    )

    def _partner_vals_from_address(self, address: dict) -> dict:
        self.ensure_one()
        country = False
        country_code = (address.get("country_code") or "").strip().upper()
        if country_code:
            country = self.env["res.country"].search(
                [("code", "=", country_code)],
                limit=1,
            )
        state = False
        state_code = (address.get("state_code") or "").strip().upper()
        if state_code and country:
            state = self.env["res.country.state"].search(
                [
                    ("country_id", "=", country.id),
                    "|",
                    ("code", "=", state_code),
                    ("code", "=", state_code[:3]),
                ],
                limit=1,
            )
        vals = {
            "street": address.get("street") or False,
            "city": address.get("city") or False,
            "zip": address.get("zip") or False,
            "phone": address.get("phone") or False,
            "email": address.get("email") or False,
            "country_id": country.id if country else False,
            "state_id": state.id if state else False,
        }
        name = (address.get("name") or "").strip()
        if name:
            vals["name"] = name
        return {key: value for key, value in vals.items() if value}

    @api.model
    def upsert_match(self, company, warehouse, address: dict, *, update_partner: bool = True):
        address_id = str(address.get("id") or "").strip()
        if not warehouse or not address_id:
            raise UserError(_("Select a warehouse and an Envia origin address."))
        vals = {
            "company_id": company.id,
            "warehouse_id": warehouse.id,
            "envia_address_id": address_id,
            "envia_address_label": address.get("label") or address_id,
            "street": address.get("street") or False,
            "city": address.get("city") or False,
            "zip": address.get("zip") or False,
            "phone": address.get("phone") or False,
            "email": address.get("email") or False,
            "country_code": address.get("country_code") or False,
            "state_code": address.get("state_code") or False,
        }
        existing = self.search([("warehouse_id", "=", warehouse.id)], limit=1)
        record = existing if existing else self.create(vals)
        if existing:
            record.write(vals)
        if update_partner:
            partner = warehouse.partner_id
            if partner:
                partner_vals = record._partner_vals_from_address(address)
                if partner_vals:
                    partner.write(partner_vals)
        return record

    def action_edit_origin(self):
        self.ensure_one()
        return self.warehouse_id.action_envia_link_origin()
