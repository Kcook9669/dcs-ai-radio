-- =============================================================================
-- DCS AI Radio - Hook Script (v2)
-- INSTALL: Copy to Saved Games\DCS\Scripts\Hooks\dcs-ai-radio-hook.lua
-- =============================================================================

local aiRadio = {}
local SHARED_DIR = lfs.writedir() .. "dcs-ai-radio\\"
local LOG_FILE = SHARED_DIR .. "hook_log.txt"

local function log(msg)
    local f = io.open(LOG_FILE, "a")
    if f then
        f:write(os.date("[%H:%M:%S] ") .. msg .. "\n")
        f:close()
    end
end

local function escapeStr(s)
    if s == nil then return "null" end
    s = string.gsub(tostring(s), '\\', '\\\\')
    s = string.gsub(s, '"', '\\"')
    s = string.gsub(s, '\n', '\\n')
    return '"' .. s .. '"'
end

local function toJSON(val)
    if val == nil then return "null"
    elseif type(val) == "boolean" then return val and "true" or "false"
    elseif type(val) == "number" then return tostring(val)
    elseif type(val) == "string" then return escapeStr(val)
    elseif type(val) == "table" then
        local isArray = (#val > 0)
        local parts = {}
        if isArray then
            for i, v in ipairs(val) do parts[#parts + 1] = toJSON(v) end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            for k, v in pairs(val) do
                parts[#parts + 1] = escapeStr(k) .. ":" .. toJSON(v)
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    end
    return "null"
end

local function writeFile(path, content)
    local tempPath = path .. ".tmp"
    local f = io.open(tempPath, "w")
    if f then
        f:write(content)
        f:close()
        os.remove(path)
        os.rename(tempPath, path)
        return true
    end
    return false
end

local function buildInjectScript(sharedDir)
    local escapedDir = sharedDir:gsub("\\", "\\\\")
    local head = 'dcsAiRadio = {}\ndcsAiRadio.sharedDir = "' .. escapedDir .. '"\n'
    local body = [[
        dcsAiRadio.commandFile  = dcsAiRadio.sharedDir .. "command.json"
        dcsAiRadio.resultFile   = dcsAiRadio.sharedDir .. "result.json"
        dcsAiRadio.pollInterval = 0.25

        dcsAiRadio.escapeStr = function(s)
            if s == nil then return "null" end
            s = string.gsub(tostring(s), '\\', '\\\\')
            s = string.gsub(s, '"', '\\"')
            s = string.gsub(s, '\n', '\\n')
            return '"' .. s .. '"'
        end

        dcsAiRadio.toJSON = function(val)
            if val == nil then return "null"
            elseif type(val) == "boolean" then return val and "true" or "false"
            elseif type(val) == "number" then return tostring(val)
            elseif type(val) == "string" then return dcsAiRadio.escapeStr(val)
            elseif type(val) == "table" then
                local isArray = (#val > 0)
                local parts = {}
                if isArray then
                    for i, v in ipairs(val) do parts[#parts + 1] = dcsAiRadio.toJSON(v) end
                    return "[" .. table.concat(parts, ",") .. "]"
                else
                    for k, v in pairs(val) do
                        parts[#parts + 1] = dcsAiRadio.escapeStr(k) .. ":" .. dcsAiRadio.toJSON(v)
                    end
                    return "{" .. table.concat(parts, ",") .. "}"
                end
            end
            return "null"
        end

        dcsAiRadio.parseJSON = function(str)
            local result = {}
            for k, v in string.gmatch(str, '"([^"]+)"%s*:%s*"([^"]*)"') do
                result[k] = v
            end
            for k, v in string.gmatch(str, '"([^"]+)"%s*:%s*(%d+%.?%d*)') do
                if not result[k] then result[k] = tonumber(v) end
            end
            for k in string.gmatch(str, '"([^"]+)"%s*:%s*true') do
                result[k] = true
            end
            for k in string.gmatch(str, '"([^"]+)"%s*:%s*false') do
                result[k] = false
            end
            return result
        end

        dcsAiRadio.writeFile = function(path, content)
            local tempPath = path .. ".tmp"
            local f = io.open(tempPath, "w")
            if f then
                f:write(content)
                f:close()
                os.remove(path)
                os.rename(tempPath, path)
                return true
            end
            return false
        end

        dcsAiRadio.getAirbases = function()
            local names = {}
            local seen = {}
            for c = 0, 2 do
                local airbases = coalition.getAirbases(c)
                if airbases then
                    for i, ab in ipairs(airbases) do
                        local name = ab:getName()
                        if name and not seen[name] then
                            seen[name] = true
                            names[#names + 1] = name
                        end
                    end
                end
            end
            return table.concat(names, ",")
        end

        dcsAiRadio.getAirbaseWeather = function(airbaseName)
            local ab = nil
            for c = 0, 2 do
                local airbases = coalition.getAirbases(c)
                if airbases then
                    for i, a in ipairs(airbases) do
                        if a:getName() == airbaseName then
                            ab = a
                            break
                        end
                    end
                end
                if ab then break end
            end
            if not ab then return "error:airbase_not_found" end
            local pos = ab:getPoint()
            if not pos then return "error:no_position" end
            local windDir = 0
            local windSpeed = 0
            local wind = atmosphere.getWind({x = pos.x, y = pos.y + 10, z = pos.z})
            if wind then
                windSpeed = math.sqrt(wind.x * wind.x + wind.z * wind.z)
                windDir = math.deg(math.atan2(wind.z, wind.x))
                windDir = (windDir + 180) % 360
            end
            local temp = 15
            local pressure = 760
            local t, p = atmosphere.getTemperatureAndPressure({x = pos.x, y = pos.y + 10, z = pos.z})
            if t then temp = t - 273.15 end
            if p then pressure = p / 133.322 end
            local windSpeedKts = windSpeed * 1.944
            return string.format("wind_from:%d,wind_speed_kts:%.1f,temp:%d,pressure:%d",
                math.floor(windDir), windSpeedKts, math.floor(temp), math.floor(pressure))
        end

        dcsAiRadio.wingmanCommand = function(commandType, groupName)
            local group = Group.getByName(groupName)
            if not group then return "error:group_not_found" end
            local controller = group:getController()
            if not controller then return "error:no_controller" end
            if commandType == "rejoin" then
                controller:resetTask()
                return "ok:wingman_rejoining"
            elseif commandType == "engage_target" then
                controller:setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.WEAPON_FREE)
                return "ok:weapons_free"
            elseif commandType == "cover_me" then
                controller:setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.RETURN_FIRE)
                return "ok:covering"
            elseif commandType == "rtb" then
                controller:resetTask()
                return "ok:rtb"
            end
            return "error:unknown_command"
        end

        dcsAiRadio.processCommand = function(cmd)
            local category = cmd.category or "none"
            local action = cmd.action or "none"
            local result = "error:unknown"
            if category == "atc" then
                if action == "get_airbases" then
                    result = dcsAiRadio.getAirbases()
                elseif action == "get_airbase_info" then
                    local airbase = cmd.airbase or ""
                    result = dcsAiRadio.getAirbaseWeather(airbase)
                end
            elseif category == "wingman" then
                local groupName = cmd.group_name or ""
                result = dcsAiRadio.wingmanCommand(action, groupName)
            elseif category == "message" then
                local text = cmd.text or "DCS AI Radio"
                local duration = cmd.duration or 10
                trigger.action.outText(text, duration)
                result = "message_sent"
            end
            dcsAiRadio.writeFile(dcsAiRadio.resultFile, dcsAiRadio.toJSON({
                category = category,
                action = action,
                result = result,
                timestamp = timer.getTime()
            }))
        end

        dcsAiRadio.poll = function(arg, time)
            local f = io.open(dcsAiRadio.commandFile, "r")
            if f then
                local content = f:read("*all")
                f:close()
                os.remove(dcsAiRadio.commandFile)
                if content and content ~= "" then
                    local ok, err = pcall(function()
                        local cmd = dcsAiRadio.parseJSON(content)
                        if cmd and cmd.category then
                            dcsAiRadio.processCommand(cmd)
                        end
                    end)
                    if not ok then
                        dcsAiRadio.writeFile(dcsAiRadio.resultFile, dcsAiRadio.toJSON({
                            category = "error",
                            action = "process",
                            result = tostring(err),
                            timestamp = timer.getTime()
                        }))
                    end
                end
            end
            return time + dcsAiRadio.pollInterval
        end        
        -- DIAGNOSTIC: write a file proving this line was reached
        local diagFile = io.open(dcsAiRadio.sharedDir .. "script_ran.txt", "w")
        if diagFile then diagFile:write("script executed at " .. tostring(timer.getTime())); diagFile:close() end
    ]]
    return head .. body
end

function aiRadio.onMissionLoadEnd()
    log("Mission loaded - injecting function definitions")
    os.execute('mkdir "' .. SHARED_DIR .. '" 2>nul')
    local script = buildInjectScript(SHARED_DIR)
    local result, err = net.dostring_in("mission", script)
    log("dostring result: [" .. tostring(result) .. "] err: [" .. tostring(err) .. "]")
    writeFile(SHARED_DIR .. "hook_status.json", toJSON({
        status = "ready", timestamp = os.time()
    }))
end

function aiRadio.onSimulationStart()
    log("Simulation started - starting poll loop")
    local result, err = net.dostring_in("mission", [[
        timer.scheduleFunction(dcsAiRadio.poll, nil, timer.getTime() + 1)
        trigger.action.outText("DCS AI Radio v3: Active", 5)
    ]])
    log("Poll loop result: [" .. tostring(result) .. "] err: [" .. tostring(err) .. "]")
end

function aiRadio.onSimulationStop()
    log("Mission stopped")
    writeFile(SHARED_DIR .. "hook_status.json", toJSON({
        status = "stopped", timestamp = os.time()
    }))
end

DCS.setUserCallbacks(aiRadio)
os.execute('mkdir "' .. SHARED_DIR .. '" 2>nul')
log("DCS AI Radio hook loaded (v3)")