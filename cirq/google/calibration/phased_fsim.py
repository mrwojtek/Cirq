from typing import Dict, MutableMapping, Optional, Tuple, TYPE_CHECKING

import abc
import collections
import dataclasses
import functools
import numpy as np
import re

from cirq.circuits import Circuit
from cirq.ops import (
    FSimGate,
    Gate,
    ISwapPowGate,
    PhasedFSimGate,
    PhasedISwapPowGate,
    Qid
)
from cirq.google.api import v2
from cirq.google.engine import CalibrationLayer, CalibrationResult
from cirq.google.serializable_gate_set import SerializableGateSet

if TYPE_CHECKING:
    from cirq.google.calibration.engine_simulator import PhasedFSimEngineSimulator

    # Workaround for mypy custom dataclasses
    from dataclasses import dataclass as json_serializable_dataclass
else:
    from cirq.protocols import json_serializable_dataclass


@json_serializable_dataclass(frozen=True)
class PhasedFSimParameters:
    theta: Optional[float] = None
    zeta: Optional[float] = None
    chi: Optional[float] = None
    gamma: Optional[float] = None
    phi: Optional[float] = None

    def asdict(self) -> Dict[str, float]:
        """Converts parameters to a dictionary that maps angles name to values.
        """
        return dataclasses.asdict(self)

    def all_none(self) -> bool:
        return (self.theta is None and
                self.zeta is None and
                self.chi is None and
                self.gamma is None and
                self.phi is None)

    def any_none(self) -> bool:
        return (self.theta is None or
                self.zeta is None or
                self.chi is None or
                self.gamma is None or
                self.phi is None)

    def for_qubits_swapped(self) -> 'PhasedFSimParameters':
        """Parameters for the gate with qubits swapped between each other.

        The angles theta, gamma and phi are kept unchanged. The angles zeta and chi are negated for
        the gate with qubits swapped.

        Returns:
            New instance with angles adjusted for swapped qubits.
        """
        chi = -self.chi if self.chi is not None else None
        zeta = -self.zeta if self.zeta is not None else None
        return PhasedFSimParameters(
            theta=self.theta,
            zeta=zeta,
            chi=chi,
            gamma=self.gamma,
            phi=self.phi
        )

    def other_when_none(self, other: 'PhasedFSimParameters') -> 'PhasedFSimParameters':
        return PhasedFSimParameters(
            theta=other.theta if self.theta is None else self.theta,
            zeta=other.zeta if self.zeta is None else self.zeta,
            chi=other.chi if self.chi is None else self.chi,
            gamma=other.gamma if self.gamma is None else self.gamma,
            phi=other.phi if self.phi is None else self.phi,
        )

    def override(self, other: 'PhasedFSimParameters') -> 'PhasedFSimParameters':
        return other.other_when_none(self)


@json_serializable_dataclass(frozen=True)
class FloquetPhasedFSimCalibrationOptions:
    estimate_theta: bool
    estimate_zeta: bool
    estimate_chi: bool
    estimate_gamma: bool
    estimate_phi: bool

    @staticmethod
    def all_options() -> 'FloquetPhasedFSimCalibrationOptions':
        return FloquetPhasedFSimCalibrationOptions(
            estimate_theta=True,
            estimate_zeta=True,
            estimate_chi=True,
            estimate_gamma=True,
            estimate_phi=True
        )

    @staticmethod
    def all_except_for_chi_options() -> 'FloquetPhasedFSimCalibrationOptions':
        return FloquetPhasedFSimCalibrationOptions(
            estimate_theta=True,
            estimate_zeta=True,
            estimate_chi=False,
            estimate_gamma=True,
            estimate_phi=True
        )


@json_serializable_dataclass(frozen=True)
class PhasedFSimCalibrationResult:
    parameters: Dict[Tuple[Qid, Qid], PhasedFSimParameters]
    gate: Gate
    gate_set: SerializableGateSet

    def override(self, parameters: PhasedFSimParameters) -> 'PhasedFSimCalibrationResult':
        return PhasedFSimCalibrationResult(
            parameters={
                pair: pair_parameters.override(parameters)
                for pair, pair_parameters in self.parameters.items()
            },
            gate=self.gate,
            gate_set=self.gate_set
        )

    def get_parameters(self, a: Qid, b: Qid) -> Optional['PhasedFSimParameters']:
        if (a, b) in self.parameters:
            return self.parameters[(a, b)]
        elif (b, a) in self.parameters:
            return self.parameters[(b, a)].for_qubits_swapped()
        else:
            return None


@json_serializable_dataclass(frozen=True)
class PhasedFSimCalibrationRequest(abc.ABC):
    gate: Gate  # Any gate which can be described by cirq.PhasedFSim
    gate_set: SerializableGateSet
    pairs: Tuple[Tuple[Qid, Qid], ...]

    @property
    @functools.lru_cache
    def qubit_pairs(self) -> MutableMapping[Qid, Tuple[Qid, Qid]]:
        # Returning mutable mapping as a cached result because it's hard to get a frozen dictionary
        # in Python...
        return collections.ChainMap(*({q: pair for q in pair} for pair in self.pairs))

    @abc.abstractmethod
    def to_calibration_layer(self, handler_name: str) -> CalibrationLayer:
        pass

    @abc.abstractmethod
    def parse_result(self, result: CalibrationResult) -> PhasedFSimCalibrationResult:
        pass


@json_serializable_dataclass(frozen=True)
class FloquetPhasedFSimCalibrationResult(PhasedFSimCalibrationResult):
    options: FloquetPhasedFSimCalibrationOptions


@json_serializable_dataclass(frozen=True)
class FloquetPhasedFSimCalibrationRequest(PhasedFSimCalibrationRequest):
    options: FloquetPhasedFSimCalibrationOptions

    def to_calibration_layer(self, handler_name: str) -> CalibrationLayer:
        circuit = Circuit([self.gate.on(*pair) for pair in self.pairs])
        return CalibrationLayer(
            calibration_type='floquet_phased_fsim_characterization',
            program=circuit,
            args={
                'est_theta': self.options.estimate_theta,
                'est_zeta': self.options.estimate_zeta,
                'est_chi': self.options.estimate_chi,
                'est_gamma': self.options.estimate_gamma,
                'est_phi': self.options.estimate_phi,
                'readout_corrections': True
            }
        )

    def parse_result(self, result: CalibrationResult) -> PhasedFSimCalibrationResult:
        decoded = collections.defaultdict(lambda: {})
        for keys, values in result.metrics['angles'].items():
            for key, value in zip(keys, values):
                match = re.match(r'(\d+)_(.+)', key)
                if not match:
                    raise ValueError(f'Unknown metric name {key}')
                index = int(match[1])
                name = match[2]
                decoded[index][name] = value

        parsed = {}
        for data in decoded.values():
            a = v2.qubit_from_proto_id(data['qubit_a'])
            b = v2.qubit_from_proto_id(data['qubit_b'])
            parsed[(a, b)] = PhasedFSimParameters(
                theta=data.get('theta_est', None),
                zeta=data.get('zeta_est', None),
                chi=data.get('chi_est', None),
                gamma=data.get('gamma_est', None),
                phi=data.get('phi_est', None)
            )

        return FloquetPhasedFSimCalibrationResult(
            parameters=parsed,
            gate=self.gate,
            gate_set=self.gate_set,
            options=self.options
        )


class IncompatibleMomentError(Exception):
    pass


def sqrt_iswap_gates_translator(gate: Gate) -> Optional[FSimGate]:
    if isinstance(gate, FSimGate):
        if not np.isclose(gate.phi, 0.0):
            return None
        angle = gate.theta
    elif isinstance(gate, ISwapPowGate):
        angle = -gate.exponent * np.pi / 2
    elif isinstance(gate, PhasedFSimGate):
        if (not np.isclose(gate.zeta, 0.0) or
                not np.isclose(gate.chi, 0.0) or
                not np.isclose(gate.gamma, 0.0) or
                not np.isclose(gate.phi, 0.0)):
            return None
        angle = gate.theta
    elif isinstance(gate, PhasedISwapPowGate):
        if not np.isclose(-gate.phase_exponent - 0.5, 0.0):
            return None
        angle = gate.exponent * np.pi / 2
    else:
        return None

    if np.isclose(angle, np.pi / 4):
        return FSimGate(theta=np.pi / 4, phi=0.0)

    return None
