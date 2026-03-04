-- =============================================================================
-- DCS AI Radio - Mission Environment Script (Updated)
-- Lives in Saved Games\DCS\Scripts\dcs-ai-radio-mission.lua
-- =============================================================================

dcsAiRadio = {}
dcsAiRadio.sharedDir = lfs.writedir() .. "dcs-ai-radio\\"
dcsAiRadio.commandFile = dcsAiRadio.sharedDir .. "command.json"
dcsAiRadio.resultFile = dcsAiRadio.sharedDir .. "result.json"
dcsAiRadio.statusFile = dcsAiRadio.sharedDir .. "hook_status.json"
dcsAiRadio.pollInterval = 0.25

-- world.getPlayerUnit is not exposed in mission env; iterate groups instead.
local function findPlayerUnit()
    for c = 0, 2 do
        for _, cat in ipairs({0, 1}) do  -- 0=AIRPLANE, 1=HELICOPTER
            local groups = coalition.getGroups(c, cat)
            if groups then
                for _, g in ipairs(groups) do
                    local units = g:getUnits()
                    if units then
                        for _, u in ipairs(units) do
                            if u:getPlayerName() ~= nil then return u end
                        end
                    end
                end
            end
        end
    end
    return nil
end

local function log(msg)
    local f = io.open(dcsAiRadio.sharedDir .. "mission_log.txt", "a")
    if f then
        f:write(os.date("[%H:%M:%S] ") .. msg .. "\n")
        f:close()
    end
end

-- Silence any AWACS units on the map so our AI handles all radar calls.
-- Groups with the AWACS attribute get AI.Option.Air.id.SILENCE = true.
local function silenceAwacs()
    for c = 0, 2 do
        local groups = coalition.getGroups(c, Group.Category.AIRPLANE)
        if groups then
            for _, g in ipairs(groups) do
                local units = g:getUnits()
                if units then
                    for _, u in ipairs(units) do
                        if u:isExist() and u:hasAttribute("AWACS") then
                            local ctrl = g:getController()
                            if ctrl then
                                ctrl:setOption(AI.Option.Air.id.SILENCE, true)
                                log("Silenced AWACS group: " .. g:getName())
                            end
                            break
                        end
                    end
                end
            end
        end
    end
end

-- Silence built-in ATC at every airbase so our AI handles all tower calls.
local function silenceAirbases()
    for c = 0, 2 do
        local bases = coalition.getAirbases(c)
        if bases then
            for _, ab in ipairs(bases) do
                ab:setRadioSilentMode(true)
                log("Silenced ATC at: " .. ab:getName())
            end
        end
    end
end

-- Tell Python we are ready!
local statusOut = io.open(dcsAiRadio.statusFile, "w")
if statusOut then
    statusOut:write('{"status":"ready"}')
    statusOut:close()
end

-- Silence built-in AWACS and ATC so our AI handles all radio calls.
silenceAwacs()
silenceAirbases()

dcsAiRadio.poll = function(arg, time)
    local ok, err = pcall(function()
    local f = io.open(dcsAiRadio.commandFile, "r")
    if f then
        local content = f:read("*all")
        f:close()
        os.remove(dcsAiRadio.commandFile)
        
        if content and content ~= "" then
            log("Received command: " .. content)
            
            local category = string.match(content, '"category"%s*:%s*"([^"]+)"')
            local action = string.match(content, '"action"%s*:%s*"([^"]+)"')
            local result = "error:unhandled"
            
            -- Command Routing
            if category == "atc" then
                if action == "get_airbases" then
                    local names = {}
                    for c = 0, 2 do
                        local bases = coalition.getAirbases(c)
                        if bases then
                            for _, ab in ipairs(bases) do table.insert(names, ab:getName()) end
                        end
                    end
                    result = table.concat(names, ",")
                elseif action == "get_airbase_info" then
                    -- Weather and position logic
                    local airbaseName = string.match(content, '"airbase"%s*:%s*"([^"]+)"')
                    local ab
                    for c = 0, 2 do
                        local bases = coalition.getAirbases(c)
                        if bases then
                            for _, a in ipairs(bases) do if a:getName() == airbaseName then ab = a; break end end
                        end
                        if ab then break end
                    end
                    if ab then
                        local pos = ab:getPoint()
                        local wind = atmosphere.getWind({x=pos.x, y=pos.y+10, z=pos.z})
                        local windSpeed = math.sqrt(wind.x*wind.x + wind.z*wind.z)
                        local windDir = (math.deg(math.atan2(wind.z, wind.x)) + 180) % 360
                        local t, p = atmosphere.getTemperatureAndPressure({x=pos.x, y=pos.y+10, z=pos.z})
                        result = string.format("wind_from:%d,wind_speed_kts:%.1f,temp:%d,pressure:%d", 
                            math.floor(windDir), windSpeed*1.944, math.floor(t-273.15), math.floor(p/133.322))
                    else
                        result = "error:airbase_not_found"
                    end
                -- ADDED: Proximity Logic for context determination
                elseif action == "get_awacs_status" then
                    -- Returns "groupname:coalition;..." for every alive AWACS group.
                    local list = {}
                    for c = 0, 2 do
                        local groups = coalition.getGroups(c, Group.Category.AIRPLANE)
                        if groups then
                            for _, g in ipairs(groups) do
                                local units = g:getUnits()
                                if units then
                                    for _, u in ipairs(units) do
                                        if u:isExist() and u:hasAttribute("AWACS") then
                                            local coa = c == 1 and "red" or (c == 2 and "blue" or "neutral")
                                            table.insert(list, g:getName() .. ":" .. coa)
                                            break
                                        end
                                    end
                                end
                            end
                        end
                    end
                    result = #list > 0 and table.concat(list, ";") or "none"
                elseif action == "get_closest_airbase" then
                    local playerUnit = findPlayerUnit()
                    -- Only run if the player is actually in a unit
                    if playerUnit == nil or not playerUnit:isExist() then
                        result = "error:no_player_in_cockpit"
                    else
                        local pPos = playerUnit:getPoint()
                        local closestName = "Unknown"
                        local minDist = 9999999
                        -- Unit.Category: 0=airplane, 1=helicopter
                        local unitDesc = playerUnit:getDesc()
                        local isHeli = unitDesc and unitDesc.category == 1
                        for c = 0, 2 do
                            local bases = coalition.getAirbases(c)
                            if bases then
                                for _, ab in ipairs(bases) do
                                    local desc = ab:getDesc()
                                    if desc then
                                        -- Airbase.Category: 0=airdrome, 1=helipad/FARP, 2=ship
                                        local valid = desc.category == 0 or (isHeli and desc.category == 1)
                                        if valid then
                                            local abPos = ab:getPoint()
                                            local dist = math.sqrt((pPos.x - abPos.x)^2 + (pPos.z - abPos.z)^2)
                                            if dist < minDist then
                                                minDist = dist
                                                closestName = ab:getName()
                                            end
                                        end
                                    end
                                end
                            end
                        end
                        result = closestName
                    end
                end
            elseif category == "message" then
                local text = string.match(content, '"text"%s*:%s*"([^"]+)"')
                trigger.action.outText(text or "DCS AI Radio", 10)
                result = "message_sent"
            end
            
            -- Write result back for the Python bridge to consume
            local out = io.open(dcsAiRadio.resultFile, "w")
            if out then
                out:write('{"category":"' .. tostring(category) .. '","action":"' .. tostring(action) .. '","result":"' .. result .. '"}')
                out:close()
                log("Wrote result: " .. result)
            end
        end
    end
    end)
    if not ok then
        log("POLL ERROR: " .. tostring(err))
    end
    return time + dcsAiRadio.pollInterval
end

-- Start the loop
timer.scheduleFunction(dcsAiRadio.poll, nil, timer.getTime() + 1)
log("DCS AI Radio Mission Script Initialized")
trigger.action.outText("DCS AI Radio (Mission API): Active", 5)