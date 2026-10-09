"""Config and options flow: whose diary it is, which AI reads photos, who gets notified, and the optional meal plan."""

from __future__ import annotations

from typing import Any

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.network import NoURLAvailableError
import voluptuous as vol

from .const import (
    CONF_AI_TASK,
    CONF_DISHES_SENSOR,
    CONF_NOTIFY,
    CONF_OPEN_PATH,
    CONF_PERSON,
    CONF_PLAN_SENSOR,
    CONF_SOURCE,
    CONF_WEBHOOK_ID,
    DOMAIN,
)
from .sources import registered


def _notify_options(hass: HomeAssistant) -> list[str]:
    return sorted(f"notify.{s}" for s in hass.services.async_services_for_domain("notify") if s.startswith("mobile_app_"))


def _webhook_address(hass: HomeAssistant, webhook_id: str) -> str:
    """The webhook's full address, or just its path when Home Assistant has no URL configured."""
    try:
        return webhook.async_generate_url(hass, webhook_id, allow_ip=False, prefer_external=True)
    except NoURLAvailableError:
        return webhook.async_generate_path(webhook_id)


def _options_schema(hass: HomeAssistant, current: dict[str, Any]) -> vol.Schema:
    def opt(key: str) -> vol.Optional:
        return vol.Optional(key, description={"suggested_value": current.get(key)})

    schema = {
        opt(CONF_AI_TASK): selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task")),
        opt(CONF_NOTIFY): selector.SelectSelector(
            selector.SelectSelectorConfig(options=_notify_options(hass), custom_value=True)
        ),
        opt(CONF_OPEN_PATH): selector.TextSelector(),
        opt(CONF_PLAN_SENSOR): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        opt(CONF_DISHES_SENSOR): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
    }
    # another integration's recipes and plan: offered only once one is registered (or already chosen)
    sources = sorted({*registered(hass), *([current[CONF_SOURCE]] if current.get(CONF_SOURCE) else [])})
    if sources:
        schema[opt(CONF_SOURCE)] = selector.SelectSelector(selector.SelectSelectorConfig(options=sources))
    return vol.Schema(schema)


class FoodDiaryConfigFlow(ConfigFlow, domain=DOMAIN):
    """One diary per person."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            person = user_input[CONF_PERSON]
            await self.async_set_unique_id(person)
            self._abort_if_unique_id_configured()
            state = self.hass.states.get(person)
            name = state.name if state else person.split(".", 1)[-1].replace("_", " ").title()
            options = {k: v for k, v in user_input.items() if k != CONF_PERSON and v}
            return self.async_create_entry(
                title=f"{name}'s food diary",
                data={CONF_PERSON: person, CONF_WEBHOOK_ID: webhook.async_generate_id()},
                options=options,
            )
        schema = vol.Schema({vol.Required(CONF_PERSON): selector.EntitySelector(selector.EntitySelectorConfig(domain="person"))})
        return self.async_show_form(step_id="user", data_schema=schema.extend(_options_schema(self.hass, {}).schema))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FoodDiaryOptionsFlow()


class FoodDiaryOptionsFlow(OptionsFlow):
    """Change the AI, notifications and meal plan sensors; shows the webhook address for phone shortcuts."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={k: v for k, v in user_input.items() if v})
        url = _webhook_address(self.hass, self.config_entry.data[CONF_WEBHOOK_ID])
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.hass, dict(self.config_entry.options)),
            description_placeholders={"webhook_url": url},
        )
