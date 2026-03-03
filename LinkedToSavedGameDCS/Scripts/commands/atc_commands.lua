CommandRegistry.register("atc", "get_airbases", function(cmd)
    local names, seen = {}, {}
    for c = 0, 2 do
        local ok, bases = pcall(coalition.getAirbases, c)
        if ok and bases then
            for _, ab in ipairs(bases) do
                local name = ab:getName()
                if name and not seen[name] then
                    seen[name] = true; names[#names+1] = name
                end
            end
        end
    end
    local result = table.concat(names, ",")
    return result ~= "" and result or "error:no_airbases"
end)

CommandRegistry.register("atc", "get_airbase_info", function(cmd)
    local ab
    for c = 0, 2 do
        local ok, bases = pcall(coalition.getAirbases, c)
        if ok and bases then
            for _, a in ipairs(bases) do
                if a:getName() == (cmd.airbase or "") then ab = a; break end
            end
        end
        if ab then break end
    end
    if not ab then return "error:airbase_not_found" end

    local pos = ab:getPoint()
    if not pos then return "error:no_position" end

    local windDir, windSpeed, temp, pressure = 0, 0, 15, 760
    local wind = atmosphere.getWind({x=pos.x, y=pos.y+10, z=pos.z})
    if wind then
        windSpeed = math.sqrt(wind.x*wind.x + wind.z*wind.z)
        windDir   = (math.deg(math.atan2(wind.z, wind.x)) + 180) % 360
    end
    local t, p = atmosphere.getTemperatureAndPressure({x=pos.x, y=pos.y+10, z=pos.z})
    if t then temp = t - 273.15 end
    if p then pressure = p / 133.322 end

    return string.format("wind_from:%d,wind_speed_kts:%.1f,temp:%d,pressure:%d",
        math.floor(windDir), windSpeed*1.944, math.floor(temp), math.floor(pressure))
end)