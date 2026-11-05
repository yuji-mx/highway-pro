import { lazy } from "react";

export const NAV = [
  { no: "01", path: "/region", key: "nav.region" },
  { no: "02", path: "/mri", key: "nav.mri" },
  { no: "03", path: "/facilities", key: "nav.list" },
  { no: "04", path: "/facility", key: "nav.facility" },
  { no: "05", path: "/ramp", key: "nav.ramp" },
  { no: "06", path: "/sections", key: "nav.sections" },
  { no: "07", path: "/search", key: "nav.search" },
  { no: "08", path: "/stats", key: "nav.stats" },
  { no: "09", path: "/compare", key: "nav.compare" },
];

export const Region = lazy(() => import("./pages/Region"));
export const MriView = lazy(() => import("./pages/MriView"));
export const FacilityList = lazy(() => import("./pages/FacilityList"));
export const FacilityDetail = lazy(() => import("./pages/FacilityDetail"));
export const RampDetail = lazy(() => import("./pages/RampDetail"));
export const Sections = lazy(() => import("./pages/Sections"));
export const Search = lazy(() => import("./pages/Search"));
export const RangeStats = lazy(() => import("./pages/RangeStats"));
export const CompareExport = lazy(() => import("./pages/CompareExport"));
