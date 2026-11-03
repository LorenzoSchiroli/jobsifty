from backend.search.advanced.brief import BriefItem

REMOTE = BriefItem(text="remote only", kind="require", source="stated", field="location_type", value="remote")
NO_CONSULTING = BriefItem(text="no consulting", kind="avoid", source="stated", field="is_consulting", value=True)
STARTUP = BriefItem(text="startup", kind="prefer", source="inferred", field="is_startup", value=True)
