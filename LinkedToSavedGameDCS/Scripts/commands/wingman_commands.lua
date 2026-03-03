CommandRegistry.register("wingman", "rejoin", function(cmd)
    local group = Group.getByName(cmd.group_name or "")
    if not group then return "error:group_not_found" end
    group:getController():resetTask()
    return "ok:wingman_rejoining"
end)

CommandRegistry.register("wingman", "engage_target", function(cmd)
    local group = Group.getByName(cmd.group_name or "")
    if not group then return "error:group_not_found" end
    group:getController():setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.WEAPON_FREE)
    return "ok:weapons_free"
end)

CommandRegistry.register("wingman", "cover_me", function(cmd)
    local group = Group.getByName(cmd.group_name or "")
    if not group then return "error:group_not_found" end
    group:getController():setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.RETURN_FIRE)
    return "ok:covering"
end)

CommandRegistry.register("wingman", "rtb", function(cmd)
    local group = Group.getByName(cmd.group_name or "")
    if not group then return "error:group_not_found" end
    group:getController():resetTask()
    return "ok:rtb"
end)