-- =============================================================================
-- DCS AI Radio - Ground Crew Commands (Phase 3 rough outline)
-- Verbal responses only for now. Actual DCS rearm/refuel requires mission-level
-- trigger integration (Phase 4+).
-- =============================================================================

CommandRegistry.register("ground_crew", "rearm_refuel", function(_) return "ok:ground_crew_responding" end)
CommandRegistry.register("ground_crew", "ground_power",  function(_) return "ok:gpu_on_the_way"         end)
