CommandRegistry.register("wingman", "rejoin", function(cmd)
    local group = Group.getByName(cmd.group_name or "")
    if not group then return "error:group_not_found" end
    local controller = group:getController()
    if not controller then return "error:no_controller" end
    local ok, err = pcall(function() controller:resetTask() end)
    return ok and "ok:wingman_rejoining" or ("error:controller_crash:" .. tostring(err))
end)

CommandRegistry.register("wingman", "engage_target", function(cmd)
    local group = Group.getByName(cmd.group_name or "")
    if not group then return "error:group_not_found" end
    local controller = group:getController()
    if not controller then return "error:no_controller" end
    local ok, err = pcall(function()
        controller:setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.WEAPON_FREE)
    end)
    return ok and "ok:weapons_free" or ("error:controller_crash:" .. tostring(err))
end)

CommandRegistry.register("wingman", "cover_me", function(cmd)
    local group = Group.getByName(cmd.group_name or "")
    if not group then return "error:group_not_found" end
    local controller = group:getController()
    if not controller then return "error:no_controller" end
    local ok, err = pcall(function()
        controller:setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.RETURN_FIRE)
    end)
    return ok and "ok:covering" or ("error:controller_crash:" .. tostring(err))
end)

CommandRegistry.register("wingman", "rtb", function(cmd)
    local group = Group.getByName(cmd.group_name or "")
    if not group then return "error:group_not_found" end
    local controller = group:getController()
    if not controller then return "error:no_controller" end
    local ok, err = pcall(function() controller:resetTask() end)
    return ok and "ok:rtb" or ("error:controller_crash:" .. tostring(err))
end)

CommandRegistry.register("wingman", "go_trail", function(cmd)
    local group = Group.getByName(cmd.group_name or "")
    if not group then return "error:group_not_found" end
    local controller = group:getController()
    if not controller then return "error:no_controller" end
    local ok, err = pcall(function() controller:resetTask() end)
    return ok and "ok:go_trail" or ("error:controller_crash:" .. tostring(err))
end)
