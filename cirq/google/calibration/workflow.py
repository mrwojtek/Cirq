from typing import Callable, List, Optional, Tuple, Union, cast

from cirq.circuits import Circuit
from cirq.ops import (
    FSimGate,
    Gate,
    GateOperation,
    MeasurementGate,
    Moment,
    Qid,
    SingleQubitGate
)
from cirq.google.calibration.engine_simulator import (
    PhasedFSimEngineSimulator
)
from cirq.google.calibration.phased_fsim import (
    FloquetPhasedFSimCalibrationOptions,
    FloquetPhasedFSimCalibrationRequest,
    IncompatibleMomentError,
    PhasedFSimCalibrationRequest,
    PhasedFSimCalibrationResult,
    sqrt_iswap_gates_translator
)
from cirq.google.engine import Engine
from cirq.google.serializable_gate_set import SerializableGateSet


def floquet_characterization_for_moment(
        moment: Moment,
        options: FloquetPhasedFSimCalibrationOptions,
        gate_set: SerializableGateSet,
        gates_translator: Callable[[Gate], Optional[FSimGate]] = sqrt_iswap_gates_translator,
        pairs_in_canonical_order: bool = False,
        pairs_sorted: bool = False
) -> Optional[FloquetPhasedFSimCalibrationRequest]:

    measurement = False
    single_qubit = False
    gate: Optional[FSimGate] = None
    pairs = []

    for op in moment:
        if not isinstance(op, GateOperation):
            raise IncompatibleMomentError(
                'Moment contains operation different than GateOperation')

        if isinstance(op.gate, MeasurementGate):
            measurement = True
        elif isinstance(op.gate, SingleQubitGate):
            single_qubit = True
        else:
            translated_gate = gates_translator(op.gate)
            if translated_gate is None:
                raise IncompatibleMomentError(
                    f'Moment {moment} contains unsupported non-single qubit operation {op}')
            elif gate is not None and gate != translated_gate:
                raise IncompatibleMomentError(
                    f'Moment {moment} contains operations resolved to two different gates {gate} '
                    f'and {translated_gate}')
            else:
                gate = translated_gate
            pair = cast(Tuple[Qid, Qid],
                        tuple(sorted(op.qubits) if pairs_in_canonical_order else op.qubits))
            pairs.append(pair)

    if gate is None:
        # Either empty, single-qubit or measurement moment.
        return None

    if gate is not None and (measurement or single_qubit):
        raise IncompatibleMomentError(f'Moment contains mixed two-qubit operations and '
                                      f'single-qubit operations or measurement operations.')

    return FloquetPhasedFSimCalibrationRequest(
        pairs=tuple(sorted(pairs) if pairs_sorted else pairs),
        gate=gate,
        options=options
    )


# TODO: Add support for ISWAP ** 0.5 as well.
# TODO: Add support for WaitGates
# TODO: Add support for CircuitOperations.
def floquet_characterization_for_circuit(
        circuit: Circuit,
        gate_set: SerializableGateSet,
        gates_translator: Callable[[Gate], Optional[FSimGate]] = sqrt_iswap_gates_translator,
        options: FloquetPhasedFSimCalibrationOptions = FloquetPhasedFSimCalibrationOptions.
            all_except_for_chi_options(),
        merge_sub_sets: bool = True,
        initial: Optional[
            Tuple[List[FloquetPhasedFSimCalibrationRequest], List[Optional[int]]]] = None
) -> Tuple[List[FloquetPhasedFSimCalibrationRequest], List[Optional[int]]]:
    """
    Returns:
        Tuple of:
          - list of calibration requests,
          - list of indices of the generated calibration requests for each
            moment in the supplied circuit. If None occurs at certain position,
            it means that the related moment was not recognized for calibration.
    """

    def append_if_missing(calibration: FloquetPhasedFSimCalibrationRequest) -> int:
        if calibration.pairs not in pairs_map:
            index = len(calibrations)
            calibrations.append(calibration)
            pairs_map[calibration.pairs] = index
            return index
        else:
            return pairs_map[calibration.pairs]

    def merge_into_calibrations(calibration: FloquetPhasedFSimCalibrationRequest) -> int:
        new_pairs = set(calibration.pairs)
        for index in pairs_map.values():
            assert calibration.gate == calibrations[index].gate
            assert calibration.options == calibrations[index].options
            existing_pairs = calibrations[index].pairs
            if new_pairs.issubset(existing_pairs):
                return index
            elif new_pairs.issuperset(existing_pairs):
                calibrations[index] = calibration
                return index
            else:
                new_qubit_pairs = calibration.qubit_pairs
                existing_qubit_pairs = calibrations[index].qubit_pairs
                if all((new_qubit_pairs[q] == existing_qubit_pairs[q]
                        for q in set(new_qubit_pairs.keys()).intersection(existing_qubit_pairs.keys()))):
                    calibrations[index] = FloquetPhasedFSimCalibrationRequest(
                        gate=calibration.gate,
                        pairs=tuple(sorted(new_pairs.union(existing_pairs))),
                        options=options
                    )
                    return index

        index = len(calibrations)
        calibrations.append(calibration)
        pairs_map[calibration.pairs] = index
        return index

    if initial is None:
        calibrations = []
        moments_map = []
    else:
        calibrations, moments_map = initial

    pairs_map = {}

    for moment in circuit:
        calibration = floquet_characterization_for_moment(moment, options, gate_set, gates_translator,
                                                          pairs_in_canonical_order=True,
                                                          pairs_sorted=True)

        if calibration is not None:
            if merge_sub_sets:
                index = merge_into_calibrations(calibration)
            else:
                index = append_if_missing(calibration)
            moments_map.append(index)
        else:
            moments_map.append(None)

    return calibrations, moments_map


def run_characterizations(calibrations: List[PhasedFSimCalibrationRequest],
                          engine: Union[Engine, PhasedFSimEngineSimulator],
                          processor_id: Optional[str] = None,
                          gate_set: Optional[SerializableGateSet] = None,
                          max_layers_per_request: int = 1,
                          progress_func: Optional[Callable[[int, int], None]] = None
                          ) -> List[PhasedFSimCalibrationResult]:
    """Runs calibration requests on the Engine.

    Args:
        calibrations: List of calibrations to perform described in a request object.
        engine: cirq.google.Engine object used for running the calibrations.
        processor_id: processor_id passed to engine.run_calibrations method.
        gate_set: Gate set to use for characterization request.

    Returns:
        List of PhasedFSimCalibrationResult for each requested calibration.
    """
    if max_layers_per_request < 1:
        raise ValueError(f'Miaximum number of layers pere request must be at least 1, '
                         f'{max_layers_per_request} given')

    if not calibrations:
        return []

    if isinstance(engine, Engine):
        if processor_id is None:
            raise ValueError('processor_id must be provided when running on the engine')
        if gate_set is None:
            raise ValueError('gate_set must be provided when running on the engine')

        results = []

        requests = [
            [calibration.to_calibration_layer()
             for calibration in calibrations[offset:offset + max_layers_per_request]]
            for offset in range(0, len(calibrations), max_layers_per_request)
        ]

        for request in requests:
            job = engine.run_calibration(request,
                                         processor_id=processor_id,
                                         gate_set=gate_set)
            request_results = job.calibration_results()
            results += [calibration.parse_result(result)
                        for calibration, result in zip(calibrations, request_results)]
            if progress_func:
                progress_func(len(results), len(calibrations))

    elif isinstance(engine, PhasedFSimEngineSimulator):
        results = engine.get_calibrations(calibrations)
    else:
        raise ValueError(f'Unsupported engine type {type(engine)}')

    return results


def run_floquet_characterization_for_circuit(
        circuit: Circuit,
        engine: Union[Engine, PhasedFSimEngineSimulator],
        processor_id: str,
        gate_set: SerializableGateSet,
        gates_translator: Callable[[Gate], Optional[FSimGate]] = sqrt_iswap_gates_translator,
        options: FloquetPhasedFSimCalibrationOptions = FloquetPhasedFSimCalibrationOptions.
            all_except_for_chi_options(),
        merge_sub_sets: bool = True,
        max_layers_per_request: int = 1,
        progress_func: Optional[Callable[[int, int], None]] = None
) -> Tuple[List[PhasedFSimCalibrationResult], List[Optional[int]]]:
    requests, mapping = floquet_characterization_for_circuit(
        circuit, gate_set, gates_translator, options, merge_sub_sets=merge_sub_sets)
    results = run_characterizations(requests, engine, processor_id,
                                    max_layers_per_request=max_layers_per_request,
                                    progress_func=progress_func)
    return results, mapping
