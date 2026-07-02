import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { Overview }       from "./pages/Overview";
import { BikeSales }      from "./pages/BikeSales";
import { McsiEDA }        from "./pages/McsiEDA";
import { UIO }            from "./pages/UIO";
import { UIOForecast }    from "./pages/UIOForecast";
import { EDA }            from "./pages/EDA";
import { OBMEDA }         from "./pages/OBMEDA";
import { PartMaster }     from "./pages/PartMaster";
import { Forecast }       from "./pages/Forecast";
import { Inventory }      from "./pages/Inventory";
import { Orders }         from "./pages/Orders";
import { Classification } from "./pages/Classification";
import { RL }             from "./pages/RL";
import { Catalog }        from "./pages/Catalog";
import { MarketBasket }  from "./pages/MarketBasket";
import { fetchPipeline, fetchPipelineFreshness, type PipelineStatus } from "./api/client";

export default function App() {
  const [pipeline, setPipeline]     = useState<PipelineStatus | null>(null);
  const [freshness, setFreshness]   = useState<Record<string, string | null>>({});

  useEffect(() => {
    fetchPipeline().then(setPipeline).catch(() => null);
    fetchPipelineFreshness().then(setFreshness).catch(() => null);
  }, []);

  return (
    <BrowserRouter>
      <div className="flex h-screen w-full font-sans bg-surface overflow-hidden">
        <Sidebar pipeline={pipeline} freshness={freshness} />
        <main className="flex-1 flex flex-col overflow-hidden">
          <Routes>
            <Route path="/"               element={<Overview />} />
            <Route path="/bikes"          element={<BikeSales />} />
            <Route path="/mcsi-eda"       element={<McsiEDA />} />
            <Route path="/uio"            element={<UIO />} />
            <Route path="/uio-forecast"   element={<UIOForecast />} />
            <Route path="/eda"            element={<EDA />} />
            <Route path="/obm-eda"        element={<OBMEDA />} />
            <Route path="/parts"          element={<PartMaster />} />
            <Route path="/classification" element={<Classification />} />
            <Route path="/forecast"       element={<Forecast />} />
            <Route path="/inventory"      element={<Inventory />} />
            <Route path="/orders"         element={<Orders />} />
            <Route path="/rl"             element={<RL />} />
            <Route path="/catalog"        element={<Catalog />} />
            <Route path="/market-basket"  element={<MarketBasket />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
