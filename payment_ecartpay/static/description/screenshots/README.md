# Capturas para la Odoo Apps Store — `payment_ecartpay`

Este directorio contiene las capturas de pantalla referenciadas desde `static/description/index.html`.

## Recomendaciones de formato

- **Formato**: PNG (con transparencia si aplica).
- **Ancho máximo**: 1280 px (la Apps Store re-escala automáticamente).
- **Peso máximo por imagen**: 500 KB (comprime con TinyPNG o similar).
- **Aspect ratio**: horizontal (16:9 o 16:10). Evita capturas verticales.
- **Idioma**: usa una instancia con interfaz en español para mantener consistencia con el copy.

## Capturas requeridas

| Archivo                                | Debe mostrar |
|----------------------------------------|--------------|
| `01-provider-configuration.png`        | Panel de Ecart Pay (Dev Tools → Credenciales) con las claves API de prueba y producción visibles (oculta valores reales con un editor). |
| `02-provider-configuration-odoo.png`   | Formulario del proveedor **Ecart Pay** en Odoo (Contabilidad → Configuración → Proveedores de pago), con las claves ya pegadas y modo de prueba/producción visible. |
| `03-checkout-payment-methods.png`      | Checkout del eCommerce de Odoo con la lista de métodos de pago; resalta la opción **Ecart Pay**. |
| `04-hosted-checkout.png`               | Página de checkout hospedado de Ecart Pay (la que ve el cliente al ser redirigido). |
| `05-transaction-confirmation.png`      | Pantalla de confirmación de pago exitoso en Odoo (`/payment/status` o retorno del portal). |
| `06-transaction-detail.png`            | Registro `payment.transaction` en Odoo mostrando la referencia, el ID de orden en Ecart Pay y el estado (`done`). |

> Cuando reemplaces cualquier archivo, mantén el nombre exacto para no romper las referencias en `index.html`.
