-- =============================================================================
-- DCS AI Radio - Export Script (File-Based)
-- Writes live mission state to a JSON file that Python reads.
-- Also handles command bridge: reads command.json, writes result.json.
--
-- INSTALL:
--   Copy this file to:
--   %USERPROFILE%\Saved Games\DCS\Scripts\Export.lua
-- =============================================================================

-- Config
local SEND_INTERVAL = 0.5  -- seconds between state updates (2 Hz)
local OUTPUT_DIR    = lfs.writedir() .. "dcs-ai-radio\\"

-- Command bridge file paths
local COMMAND_FILE = OUTPUT_DIR .. "command.json"
local RESULT_FILE  = OUTPUT_DIR .. "result.json"

-- State
local lastSendTime = 0

-- =============================================================================
-- JSON ENCODER (minimal, no external dependency)
-- =============================================================================
local function escapeStr(s)
    if s == nil then return "null" end
    s = string.gsub(tostring(s), '\\', '\\\\')
    s = string.gsub(s, '"', '\\"')
    s = string.gsub(s, '\n', '\\n')
    return '"' .. s .. '"'
end

local function toJSON(val)
    if val == nil then
        return "null"
    elseif type(val) == "boolean" then
        return val and "true" or "false"
    elseif type(val) == "number" then
        return tostring(val)
    elseif type(val) == "string" then
        return escapeStr(val)
    elseif type(val) == "table" then
        local isArray = (#val > 0)
        local parts = {}
        if isArray then
            for i, v in ipairs(val) do
                parts[#parts + 1] = toJSON(v)
            end
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
    -- Atomic write: write to .tmp then rename
    local tmp = path .. ".tmp"
    local f = io.open(tmp, "w")
    if f then
        f:write(content)
        f:close()
        os.remove(path)
        os.rename(tmp, path)
        return true
    end
    return false
end

CommandRegistry = {}
CommandRegistry._handlers = {}

function CommandRegistry.register(category, action, fn)
    if not CommandRegistry._handlers[category] then
        CommandRegistry._handlers[category] = {}
    end
    CommandRegistry._handlers[category][action] = fn
end

function CommandRegistry.get(category, action)
    local cat = CommandRegistry._handlers[category]
    return cat and cat[action] or nil
end

-- =============================================================================
-- COMMAND BRIDGE - JSON parser (minimal, handles flat key/value only)
-- =============================================================================
local function parseJSON(str)
    local result = {}
    for k, v in str:gmatch('"([^"]+)"%s*:%s*"([^"]*)"')   do result[k] = v            end
    for k, v in str:gmatch('"([^"]+)"%s*:%s*([%d%.%-]+)') do
        if not result[k] then result[k] = tonumber(v) end
    end
    for k in str:gmatch('"([^"]+)"%s*:%s*true')  do result[k] = true  end
    for k in str:gmatch('"([^"]+)"%s*:%s*false') do result[k] = false end
    return result
end

local function writeResult(cat, action, result)
    writeFile(RESULT_FILE, toJSON({
        category  = cat,
        action    = action,
        result    = result,
        timestamp = LoGetModelTime() or 0
    }))
end

-- =============================================================================
-- COMMAND BRIDGE - Command handlers
-- These run inside the export environment which has io, coalition, atmosphere.
-- =============================================================================
local COMMANDS_DIR = lfs.writedir() .. "Scripts\\commands\\"

local function loadCommandAddons()
    local ok, iter = pcall(lfs.dir, COMMANDS_DIR)
    if not ok then return end
    for filename in iter do
        if filename:match("%.lua$") then
            pcall(dofile, COMMANDS_DIR .. filename)
        end
    end
end

-- In LuaExportStart(), add:
loadCommandAddons()

local function processCommand(cmd)
    local cat     = cmd.category or "none"
    local action  = cmd.action   or "none"
    local handler = CommandRegistry.get(cat, action)
    local result

    if handler then
        local ok, ret = pcall(handler, cmd)
        result = ok and ret or ("error:handler_crash:" .. tostring(ret))
    else
        result = "error:no_handler_for_" .. cat .. "/" .. action
    end

    writeResult(cat, action, result)
end

local function pollCommands()
    local f = io.open(COMMAND_FILE, "r")
    if f then
        local content = f:read("*all")
        f:close()
        os.remove(COMMAND_FILE)
        if content and content ~= "" then
            local ok, err = pcall(function()
                local cmd = parseJSON(content)
                if cmd and cmd.category then
                    processCommand(cmd)
                end
            end)
            if not ok then
                writeResult("error", "process", tostring(err))
            end
        end
    end
end

-- =============================================================================
-- DATA GATHERING - Mission state for Python context injection
-- =============================================================================
local function getPlayerData()
    local self = LoGetSelfData()
    if self == nil then return nil end

    return {
        callsign      = self.UnitName or self.Name or "Unknown",
        type          = self.Name or "Unknown",
        coalition     = self.Coalition or "Unknown",
        country       = self.Country or "Unknown",
        position      = {
            lat   = self.LatLongAlt and self.LatLongAlt.Lat  or 0,
            lon   = self.LatLongAlt and self.LatLongAlt.Long or 0,
            alt_m = self.LatLongAlt and self.LatLongAlt.Alt  or 0
        },
        heading       = LoGetHeading             and LoGetHeading()             or 0,
        ias_mps       = LoGetIndicatedAirSpeed   and LoGetIndicatedAirSpeed()   or 0,
        altitude_asl  = LoGetAltitudeAboveSeaLevel    and LoGetAltitudeAboveSeaLevel()    or 0,
        altitude_agl  = LoGetAltitudeAboveGroundLevel and LoGetAltitudeAboveGroundLevel() or 0,
        mach          = LoGetMachNumber          and LoGetMachNumber()          or 0,
        fuel_internal = LoGetEngineInfo          and LoGetEngineInfo().fuel_internal or 0
    }
end

local function getWorldObjects()
    local objects = {}
    local raw = LoGetWorldObjects("units") or {}

    for id, obj in pairs(raw) do
        objects[#objects + 1] = {
            id        = id,
            name      = obj.Name     or "Unknown",
            type      = obj.Name     or "Unknown",
            coalition = obj.Coalition or "Unknown",
            country   = obj.Country  or "Unknown",
            position  = {
                lat   = obj.LatLongAlt and obj.LatLongAlt.Lat  or 0,
                lon   = obj.LatLongAlt and obj.LatLongAlt.Long or 0,
                alt_m = obj.LatLongAlt and obj.LatLongAlt.Alt  or 0
            },
            heading = obj.Heading or 0,
            speed   = obj.Speed   or 0,
            flags   = {
                human = obj.Flags and obj.Flags.Human or false,
                ai    = obj.Flags and obj.Flags.AI    or false,
                born  = obj.Flags and obj.Flags.Born  or false
            }
        }
    end

    return objects
end

local function getThreatInfo()
    local threats = {}
    local tws = LoGetTWSInfo and LoGetTWSInfo() or nil

    if tws and tws.Emitters then
        for i, emitter in pairs(tws.Emitters) do
            threats[#threats + 1] = {
                type        = emitter.Type and emitter.Type.level3 or "Unknown",
                azimuth     = emitter.Azimuth   or 0,
                priority    = emitter.Priority  or 0,
                signal_type = emitter.SignalType or "Unknown"
            }
        end
    end

    return threats
end

local function getMissionTime()
    return LoGetModelTime() or 0
end

-- =============================================================================
-- EXPORT CALLBACKS
-- =============================================================================
function LuaExportStart()
    os.execute('mkdir "' .. OUTPUT_DIR .. '" 2>nul')

    writeFile(OUTPUT_DIR .. "state.json", toJSON({
        event     = "start",
        timestamp = 0
    }))

    local logFile = io.open(OUTPUT_DIR .. "log.txt", "w")
    if logFile then
        logFile:write("DCS AI Radio export started at " .. os.date() .. "\n")
        logFile:write("Output dir: " .. OUTPUT_DIR .. "\n")
        logFile:close()
    end

    lastSendTime = 0
end

function LuaExportActivityNextEvent(t)
    -- Always poll for commands on every tick (every 0.05s)
    pollCommands()

    -- State export runs at the slower SEND_INTERVAL rate
    if t - lastSendTime >= SEND_INTERVAL then
        lastSendTime = t

        local player = getPlayerData()
        if player then
            local state = {
                event     = "state",
                timestamp = getMissionTime(),
                player    = player,
                units     = getWorldObjects(),
                threats   = getThreatInfo()
            }
            writeFile(OUTPUT_DIR .. "state.json", toJSON(state))
        end
    end

    return t + 0.05
end

function LuaExportStop()
    writeFile(OUTPUT_DIR .. "state.json", toJSON({
        event     = "stop",
        timestamp = getMissionTime()
    }))
end

function LuaExportBeforeNextFrameRender() end
function LuaExportAfterNextFrameRender()  end