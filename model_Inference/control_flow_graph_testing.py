
import torch.nn as nn
import torch
import inspect
from network_profiler import profileNetwork
from control_flow_graph import build_control_flow_graph, Graph
from METIS_graph_partition import part_graph, METIS_Error, mobjtype_et
from pprint import pprint
from res_net_example import resnet20_cifar
from IPython.core.display import display_svg
from collections import namedtuple


def partition_model(model, num_gpus, *sample_batch, num_iter=4, max_depth=100, basic_blocks=None, device="cuda", weights=None, wrappers=None):

    if weights is None:
        weights = profileNetwork(model, *sample_batch, max_depth=max_depth,
                                 basic_block=basic_blocks, device=device, num_iter=num_iter)

    graph = build_control_flow_graph(
        model, *sample_batch, max_depth=max_depth, weights=weights, basic_block=basic_blocks, device=device)

    adjlist = graph.adjacency_list()
    nodew = graph.get_weights()

    assert(len(adjlist) == len(nodew))

    weights = [weight_func(w) for w in nodew]

    # cuts, parts = part_graph(
    #     adjlist, nparts=num_gpus, algorithm="metis", nodew=weights, contig=1)

    # graph.set_partition(parts)
    return graph, [], []


# TODO decide on weighting functional
def weight_func(w):
    if isinstance(w, tuple):
        return int(100*(w.forward_time+w.backward_time)/2)
    return w//100


def partition_cost(weights, parts, ncuts):
    parts_w = [0 for i in range(ncuts)]
    total_cost = 0
    for n, p in parts:
        parts_w[p] += weights[n]
        total_cost += weights[n]

    avg = total_cost/ncuts
    loss = 0
    for w in parts_w:
        loss += abs(avg-w)
    return loss


# TODO make it simpler
def find_partition(adjlist, nparts, weights, seeds):
    best_loss = torch.inf
    config = namedtuple("partition_config", "algorithm,contig,seed,parts")
    best_config = ()
    for seed in seeds:
        for algorithm in ["metis", "metis_recursive"]:
            if algorithm == "metis_recursive":
                try:
                    cuts, parts = part_graph(
                        adjlist, nparts=nparts, algorithm=algorithm, nodew=weights, contig=contig, seed=seed, objtype=objtype)
                    loss = partition_cost(adjlist, parts, cuts)
                    if loss <= best_loss:
                        best_loss = loss
                        best_config = config(
                            algorithm, contig, seed, parts)
                except METIS_Error as _:
                    continue
            else:
                for contig in [0, 1]:
                    for objtype in [mobjtype_et.METIS_OBJTYPE_CUT, mobjtype_et.METIS_OBJTYPE_VOL]:
                        if algorithm == "metis_recursive":
                            try:
                                cuts, parts = part_graph(
                                    adjlist, nparts=nparts, algorithm=algorithm, nodew=weights, contig=contig, seed=seed, objtype=objtype)
                                loss = partition_cost(adjlist, parts, cuts)
                                if loss <= best_loss:
                                    best_loss = loss
                                    best_config = config(
                                        algorithm, contig, seed, parts)
                            except METIS_Error as _:
                                continue
    return best_config


class Complex_Model(nn.Module):
    def __init__(self):
        super(Complex_Model, self).__init__()
        self.register_buffer("com_buff", torch.ones(10, 10))
        self.register_parameter(
            "comm_param", torch.nn.Parameter(torch.zeros(10, 10)))
        self.sub_1 = Branched_Model(torch.ones(1, 1))
        self.sub_2 = Branched_Model(torch.ones(2, 2))

    def forward(self, x, y):
        return self.sub_1(x) * self.comm_param.norm(), self.sub_2(y) + self.com_buff.norm()


class Branched_Model(nn.Module):
    def __init__(self, branch_buffer):
        super(Branched_Model, self).__init__()
        self.register_buffer("branch_buff", branch_buffer)
        self.register_parameter(
            "branch_param", torch.nn.Parameter(torch.zeros_like(branch_buffer)))
        # the branches can be a more complicated chains
        self.branch_1 = nn.Sequential(
            nn.Linear(1, 2), nn.ReLU(), nn.Linear(2, 1), nn.Linear(1, 1))
        self.branch_2 = nn.Sequential(nn.Sigmoid())

        self.relu = nn.ReLU()

    def forward(self, x):

        # we cannot determine if branches are independent or not
        branch_1_out = self.branch_1(x)*5+2+1*8/2
        branch_2_out = self.branch_2(x)*7*5*5*6*7*8*10/5

        # combine branches
        # our partition must put both branch outputs on the same device if not an error would occur
        # another problem is that an operation like this is "invisible" to us
        combined_branches = branch_1_out + branch_2_out+self.branch_buff.norm()

        # we cannot know that relu input is the combined output of both layers
        out = self.relu(combined_branches)
        out += self.branch_param.norm()

        return out


# infer control flow via scope name we can get it from the model and from the trace graph
# thus we can walk the graph and the names will tell us if we have a route from layer to layer
# names can be obtained easily and they represent scope (depth) and
max_depth = 100
branched_model = Branched_Model(torch.zeros(100, 100))
branched_graph, _, _ = partition_model(
    branched_model, 4, torch.zeros(1, 1), max_depth=max_depth)
branched_graph.save(f"branched_model depth{max_depth}", show_buffs_params=True)

complex_model = Complex_Model()
complex_graph, _, _ = partition_model(
    complex_model, 4, torch.zeros(1, 1), torch.zeros(1, 1), max_depth=max_depth)
complex_graph.save(f"complex model depth{max_depth}", show_buffs_params=True)

res_model = resnet20_cifar()
res_graph, _, _ = partition_model(
    res_model, 4, torch.zeros(32, 3, 32, 32), max_depth=max_depth)
res_graph.save(
    f"resnet model with cycle depth{max_depth}", show_buffs_params=True)
