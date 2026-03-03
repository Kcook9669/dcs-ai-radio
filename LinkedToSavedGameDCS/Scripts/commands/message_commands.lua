CommandRegistry.register("message", "display", function(cmd)
    local ok = pcall(trigger.action.outText, cmd.text or "DCS AI Radio", cmd.duration or 10)
    return ok and "message_sent" or "error:trigger_unavailable"
end)