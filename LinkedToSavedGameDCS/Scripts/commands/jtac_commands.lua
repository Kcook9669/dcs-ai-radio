-- =============================================================================
-- DCS AI Radio - JTAC Commands (Phase 3 rough outline)
-- Target data is computed in Python from state.json (BattlefieldState).
-- These stubs satisfy the CommandRegistry so the system doesn't return
-- "error:unhandled" for JTAC calls. Full Lua-side JTAC in Phase 4+.
-- =============================================================================

CommandRegistry.register("jtac", "check_in",      function(_) return "ok:jtac_on_station"  end)
CommandRegistry.register("jtac", "request_target", function(_) return "ok:use_python_state" end)
CommandRegistry.register("jtac", "cleared_hot",   function(_) return "ok:cleared_hot"      end)
CommandRegistry.register("jtac", "abort",          function(_) return "ok:abort"            end)
