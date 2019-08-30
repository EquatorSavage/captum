#!/usr/bin/env python3

import torch
import numpy as np

from .approximation_methods import SUPPORTED_METHODS


# TODO remove - most probably not needed
def maximum_of_lists(*lsts, abs_val=False):
    if abs_val:
        max_lst = [abs(max(sub_lst, key=abs)) for sub_lst in lsts]
    else:
        max_lst = [max(sub_lst, key=abs) for sub_lst in lsts]
    return max(max_lst, key=abs)


# TODO remove - most probably not needed
def normalize(*inputs, abs_val=False):
    max_absolute_value = maximum_of_lists(*inputs, abs_val=abs_val)
    return tuple(
        0.0 * input if max_absolute_value == 0.0 else input / max_absolute_value
        for input in inputs
    )


def random_baseline(self, input, start, end):
    return torch.tensor(
        start + end * np.random.random(input.shape),
        dtype=input.dtype,
        device=input.device,
    )


# TODO rename maybe to validate_ig_input
def validate_input(inputs, baselines, n_steps=50, method="riemann_trapezoid"):
    assert len(inputs) == len(baselines), (
        "Input and baseline must have the same "
        "dimensions, baseline has {} features whereas input has {}.".format(
            len(baselines), len(inputs)
        )
    )
    for input, baseline in zip(inputs, baselines):
        assert (
            input.shape == baseline.shape
        ), "Input and baseline must have the same shape. {} != {}".format(
            baseline.shape, input.shape
        )
    assert (
        n_steps >= 0
    ), "The number of steps must be a positive integer. " "Given: {}".format(n_steps)

    assert method in SUPPORTED_METHODS, (
        "Approximation method must be one for the following {}. "
        "Given {}".format(SUPPORTED_METHODS, method)
    )


def validate_reg_type(reg_type):
    assert reg_type in ["smoothgrad", "vargrad"], (
        "Regularization types must be either `smoothgrad` or `vargrad`. "
        "Given {}".format(reg_type)
    )


# TODO write test case here
def format_input(inputs):
    if not isinstance(inputs, tuple):
        assert isinstance(
            inputs, torch.Tensor
        ), "`inputs` must have type " "torch.Tensor but {} found: ".format(type(inputs))
        inputs = (inputs,)
    return inputs


def _format_additional_forward_args(additional_forward_args):
    if additional_forward_args is not None and not isinstance(
        additional_forward_args, tuple
    ):
        additional_forward_args = (additional_forward_args,)
    return additional_forward_args


def format_baseline(baselines, inputs):
    if baselines is None:
        baselines = zeros(inputs)

    if not isinstance(baselines, tuple):
        baselines = (baselines,)

    return baselines


def _format_input_baseline(inputs, baselines):
    inputs = format_input(inputs)
    baselines = format_baseline(baselines, inputs)
    return inputs, baselines


def _format_attributions(is_inputs_tuple, attributions):
    r"""
    In case input is a tensor and the attributions is returned in form of a
    tensor we take the first element of the attributions' tuple to match the
    same shape signatues of the inputs
    """
    assert isinstance(attributions, tuple), "Attributions must be in shape of a tuple"
    assert is_inputs_tuple or len(attributions) == 1, (
        "The input is a single tensor however the attributions aren't."
        "The number of attributed tensors is: {}".format(len(attributions))
    )
    return attributions if is_inputs_tuple else attributions[0]


def zeros(inputs):
    r"""
    Takes a tuple of tensors as input and returns a tuple that has the same
    size as the `inputs` which contains zero tensors of the same
    shape as the `inputs`

    """
    return tuple(0 * input for input in inputs)


def _extend_index_list(dim_max, base_index):
    r"""
    Returns list of index tuples in the form [(0, base_index_tuple),
    (1, base_index_tuple), ... (dim_max, base_index_tuple)]
    where base_index_tuple is either an int or tuple of arbitrary length.
    """
    assert isinstance(base_index, tuple) or isinstance(
        base_index, int
    ), "Base index must be either an integer or tuple"
    if isinstance(base_index, int):
        base_index = (base_index,)
    return [(ind,) + base_index for ind in range(dim_max)]


def _reshape_and_sum(tensor_input, num_steps, num_examples, layer_size):
    # Used for attribution methods which perform integration
    # Sums across integration steps by reshaping tensor to
    # (num_steps, num_examples, (layer_size)) and summing over
    # dimension 0. Returns a tensor of size (num_examples, (layer_size))
    return torch.sum(
        tensor_input.reshape((num_steps, num_examples) + layer_size), dim=0
    )


def _run_forward(forward_func, inputs, target=None, additional_forward_args=None):
    # make everything a tuple so that it is easy to unpack without
    # using if-statements
    inputs = format_input(inputs)
    additional_forward_args = _format_additional_forward_args(additional_forward_args)

    output = forward_func(
        *(*inputs, *additional_forward_args)
        if additional_forward_args is not None
        else inputs
    )

    return output if target is None else output[:, target]


def _expand_additional_forward_args(additional_forward_args, n_steps):
    def _expand_tensor_forward_arg(additional_forward_arg, n_steps):
        if len(additional_forward_arg.size()) == 0:
            return additional_forward_arg
        return torch.cat([additional_forward_arg] * n_steps, dim=0)

    return tuple(
        _expand_tensor_forward_arg(additional_forward_arg, n_steps)
        if isinstance(additional_forward_arg, torch.Tensor)
        else additional_forward_arg
        for additional_forward_arg in additional_forward_args
    )


def _forward_layer_eval(forward_func, inputs, layer, additional_forward_args=None):
    saved_layer_output = None

    # Set a forward hook on specified module and run forward pass to
    # get layer output tensor.
    def forward_hook(module, inp, out):
        nonlocal saved_layer_output
        saved_layer_output = out

    hook = layer.register_forward_hook(forward_hook)
    _run_forward(forward_func, inputs, additional_forward_args=additional_forward_args)
    hook.remove()
    return saved_layer_output


class MaxList:
    """Keep track of N maximal items

    Implementation of MaxList:
        for keeping track of the N top values of a large collection of items.
        Maintains a sorted list of the top N items that can be fetched with
        getlist().

    Example use:
        m = MaxList(2, key=lamda x: len(x))
        ml.add("Hello World")
        ml.add("Mermaid Man!!!!")
        ml.add("Why?")
        ml.getlist() -> ["Mermaid Man!!!!", "Hello World"]

    If storing values that are not comparable, please provide a key function that
        that maps the values to some numeric value.
    """

    def __init__(self, size, key=lambda x: x):
        self.size = size
        self.key = key
        self.list = []

    def add(self, item):
        """Add an element to the MaxList

        Args:
            item: the item that you want to add to the MaxList
        """
        value = self.key(item)
        if len(self.list) < self.size:
            if len(self.list) == 0:
                self.list.append((value, item))
            elif self.list[-1][0] >= value:
                self.list.append((value, item))
            else:
                self._insert(item, value)
        if self.list[-1][0] < value:
            self._insert(item, value)

    def get_list(self):
        """Retrive the list of N maximal items in sorted order

        Returns:
            list: the sorted list of maximal items
        """
        return [item[1] for item in self.list]

    def _insert(self, item, value):
        if len(self.list) == 0:
            self.list.append((value, item))

        for i in range(len(self.list)):
            if self.list[i][0] < value:
                self.list.insert(i, (value, item))
                break
        self.list = self.list[: self.size]


class Stat:
    """Keep track of statistics for a quantity that is measured live

    Implementation of an online statistics tracker, Stat:
        For a memory efficient way of keeping track of statistics on a large set of
        numbers. Adding numbers to the object will update the values stored in the
        object to reflect the statistics of all numbers that the object has seen
        so far.

    Example usage:
        s = Stat()
        s([5,7]) OR s.update([5,7])
        stats.get_mean() -> 6
        stats.get_std() -> 1

    """

    def __init__(self):
        self.count = 0
        self.mean = 0
        self.mean_squared_error = 0
        self.min = float("inf")
        self.max = float("-inf")

    def _std_size_check(self):
        if self.count < 2:
            raise Exception(
                "Std/Variance is not defined for {} datapoints\
                ".format(
                    self.count
                )
            )

    def update(self, x):
        """Update the stats given a new number

        Adds x to the running statistics being kept track of, and updates internal
        values that relfect that change.

        Args:
            x: a numeric value, or a list of numeric values
        """
        if isinstance(x, list):
            for value in x:
                self.update(value)
        else:
            x = float(x)
            self.min = min(self.min, x)
            self.max = max(self.max, x)
            self.count += 1
            delta = x - self.mean
            self.mean += delta / self.count
            delta2 = x - self.mean
            self.mean_squared_error += delta * delta2

    def get_stats(self):
        """Retrieves a dictionary of statistics for the values seen.

        Returns:
            a fully populated dictionary for the statistics that have been
            maintained. This output is easy to pipe into a table with a loop over
            key value pairs.
        """
        self._std_size_check()

        sampleVariance = self.mean_squared_error / (self.count - 1)
        Variance = self.mean_squared_error / self.count

        return {
            "mean": self.mean,
            "sample_variance": sampleVariance,
            "variance": Variance,
            "std": Variance ** 0.5,
            "min": self.min,
            "max": self.max,
            "count": self.count,
        }

    def get_std(self):
        """get the std of the statistics kept"""
        self._std_size_check()
        return (self.mean_squared_error / self.count) ** 0.5

    def get_variance(self):
        """get the variance of the statistics kept"""
        self._std_size_check()
        return self.mean_squared_error / self.count

    def get_sample_variance(self):
        """get the sample variance of the statistics kept"""
        self._std_size_check()
        return self.mean_squared_error / (self.count - 1)

    def get_mean(self):
        """get the mean of the statistics kept"""
        return self.mean

    def get_max(self):
        """get the max of the statistics kept"""
        return self.max

    def get_min(self):
        """get the min of the statistics kept"""
        return self.min

    def get_count(self):
        """get the count of the statistics kept"""
        return self.count
