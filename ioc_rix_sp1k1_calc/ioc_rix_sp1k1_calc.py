import importlib.util
import sys

import numpy as np

from caproto import ChannelData, ChannelType
from caproto.asyncio.client import Context
from caproto.server import AsyncLibraryLayer, PVGroup, pvproperty

# Ignore motor moves smaller than this number
DEADBAND = 0.05
# Load path for scientist-modifiable utilities file
DYNAMIC_PATH = "/cds/home/opr/rixopr/scripts/rix_utilities.py"
DYNAMIC_NAME = "rix_utilities"


class NoneRixDB:
    """
    Replacement for rix.db hutch-python imports.

    We need to ignore any such imports if they are present in rix_utilities.py
    because this SIOC needs to start up without loading the entire rix
    beamline.
    """
    def __getattr__(self, name):
        return None


# Fool Python into thinking rix.db has already been imported and is this
sys.modules["rix.db"] = NoneRixDB()
# Import the one specific file without ruining our python path
# https://docs.python.org/3/library/importlib.html#importing-a-source-file-directly
spec = importlib.util.spec_from_file_location(DYNAMIC_NAME, DYNAMIC_PATH)
rix_utilities = importlib.util.module_from_spec(spec)
sys.modules[DYNAMIC_NAME] = rix_utilities
spec.loader.exec_module(rix_utilities)


class Ioc_rix_sp1k1_calc(PVGroup):
    """
    ioc-rix-sp1k1-calc.
    """
    energy = pvproperty(
        value=0.0,
        name="ENERGY",
        record="ai",
        read_only=True,
        doc="Calculated SP1K1 Mono energy in eV",
        precision=3,
        units="eV",
        )
    cff = pvproperty(
        value=0.0,
        name="CFF",
        record="ai",
        read_only=True,
        doc="Cff number",
        precision=3,
        )
    bandwidth = pvproperty(
        value=0.0,
        name="BANDWIDTH",
        record="ai",
        read_only=True,
        doc="SP1K1 bandwidth in eV",
        precision=3,
        units="eV",
        )
    grating = pvproperty(
        value="",
        dtype=ChannelType.STRING,
        name="GRATING",
        record="stringin",
        read_only=True,
        doc="Which grating is in use",
        )

    def __init__(self, *args, **kwargs):
        self.g_pi_value = None
        self.m_pi_value = None
        self.exit_gap_value = None
        self.g_h_value = None
        super().__init__(*args, **kwargs)

    async def __ainit__(self, async_lib):
        """
        Set up async monitoring and callbacks of the critical mono PVs.

        This must be called as the startup hook when we run the server.
        """
        self.client_context = Context()

        self.g_pi_pv, self.m_pi_pv, self.exit_gap_pv, self.g_h_pv = await self.client_context.get_pvs(
            "SP1K1:MONO:MMS:G_PI.RBV",
            "SP1K1:MONO:MMS:M_PI.RBV",
            "SL1K2:EXIT:MMS:GAP.RBV",
            "SP1K1:MONO:MMS:G_H.RBV",
        )

        self.g_pi_sub = self.g_pi_pv.subscribe(data_type="time")
        self.g_pi_sub.add_callback(self._g_pi_callback)

        self.m_pi_sub = self.m_pi_pv.subscribe(data_type="time")
        self.m_pi_sub.add_callback(self._m_pi_callback)

        self.exit_gap_sub = self.exit_gap_pv.subscribe(data_type="time")
        self.exit_gap_sub.add_callback(self._exit_gap_callback)

        self.g_h_pv = self.g_h_pv.subscribe(data_type="time")
        self.g_h_pv.add_callback(self._g_h_callback)

    async def _g_pi_callback(self, pv, response):
        """
        Update calculations that use the grating pitch position.
        """
        if self.g_pi_value is None or not np.isclose(self.g_pi_value, response.data, rtol=0, atol=DEADBAND):
            self.g_pi_value = response.data
            await self._update_energy_calc(response.metadata.timestamp)
            await self._update_bandwidth_calc(response.metadata.timestamp)

    async def _m_pi_callback(self, pv, response):
        """
        Update calculations that use the mirror pitch position.
        """
        if self.m_pi_value is None or not np.isclose(self.m_pi_value, response.data, rtol=0, atol=DEADBAND):
            self.m_pi_value = response.data
            await self._update_energy_calc(response.metadata.timestamp)
            await self._update_bandwidth_calc(response.metadata.timestamp)

    async def _exit_gap_callback(self, pv, response):
        """
        Update calculations that use the exit slit gap length.
        """
        if self.exit_gap_value is None or not np.isclose(self.exit_gap_value, response.data, rtol=0, atol=DEADBAND):
            self.exit_gap_value = response.data
            await self._update_bandwidth_calc(response.metadata.timestamp)

    async def _g_h_callback(self, pv, response):
        """
        Update calculations that use the grating horizontal position.
        """
        if self.g_h_value is None or not np.isclose(self.g_h_value, response.data, rtol=0, atol=DEADBAND):
            self.g_h_value = response.data
            await self._update_grating_calc(response.metadata.timestamp)

    async def _update_energy_calc(self, timestamp):
        """
        Update our energy and cff PVs based on the most recent values.
        """
        new_energy, new_cff = self.calculate_energy()
        await self.energy.write(new_energy, timestamp=timestamp)
        await self.cff.write(new_cff, timestamp=timestamp)

    def calculate_energy(self) -> tuple[float, float]:
        """
        Run the rix_utilities calculation for the energy and cff values.
        """
        if None in (self.g_pi_value, self.m_pi_value):
            return (0, 0)
        return rix_utilities.calc_E(self.g_pi_value, self.m_pi_value)

    async def _update_bandwidth_calc(self, timestamp):
        """
        Update our bandwidth PV based on the most recent values.
        """
        new_bandwidth = self.calculate_bandwidth()
        await self.bandwidth.write(new_bandwidth, timestamp=timestamp)

    def calculate_bandwidth(self) -> float:
        """
        Run the rix_utilities calculation for the mono bandwidth.
        """
        if None in (self.g_pi_value, self.m_pi_value, self.exit_gap_value):
            return 0
        return rix_utilities.calc_BW(self.exit_gap_value, self.g_pi_value, self.m_pi_value)

    async def _update_grating_calc(self, timestamp):
        """
        Update our grating identity PV based on the most recent values.
        """
        new_grating = self.calculate_grating()
        await self.grating.write(new_grating, timestamp=timestamp)

    def calculate_grating(self) -> str:
        """
        Run the rix_utilities calculation for the grating identi.
        """
        if self.g_h_value is None:
            return ""
        return rix_utilities.get_grating()