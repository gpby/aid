import numpy as np

import sys
import argparse
import itertools
from collections import OrderedDict
from multiprocessing import Pool

from common import baseline_retrieval
from aid import automatic_image_disambiguation, hard_cluster_selection
from clue import clue
from utils import get_dataset_queries, print_metrics, ptqdm
import eval_metrics



## Cluster Selection Oracles ##


def select_clusters_by_precision(query, clusters, min_precision = 0.5):
    
    selection = {}
    for i, c in enumerate(clusters):
        precision = sum(1 for id in c if id in query['relevant']) / float(len(c))
        if precision >= min_precision:
            selection[i] = precision
    return sorted(selection.keys(), key = lambda i: selection[i], reverse = True)


def select_best_cluster(query, clusters):
    
    return select_clusters_by_precision(query, clusters, 0.0)[:1]



## Query Disambiguation Methods ##

METHODS = OrderedDict((
    ('Baseline', baseline_retrieval),
    ('CLUE', clue),
    ('Hard-Select', hard_cluster_selection),
    ('AID', automatic_image_disambiguation)
))



if __name__ == '__main__':
    
    np.random.seed(0)
    
    # Set up CLI argument parser
    parser = argparse.ArgumentParser(description = 'Evaluates the performance of various techniques for automatic image query disambiguation.', formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    parser_general = parser.add_argument_group('General')
    parser_general.add_argument('methods', type = str, nargs = '*', default = list(METHODS.keys()), help = 'Methods to be tested (choices: {}).'.format(', '.join(list(METHODS.keys()))))
    parser_general.add_argument('--num_preview', type = int, default = 10, help = 'Number of images shown to the user for each suggested image sense.')
    parser_general.add_argument('--multiple', action = 'store_const', const = True, default = False, help = 'Allow selection of multiple suggested image senses.')
    parser_general.add_argument('--rounds', type = int, default = 5, help = 'Number of iterations of the experiment for more robustness against random initializations. Results will be averaged.')
    parser_data = parser.add_argument_group('Data')
    parser_data.add_argument('--gt_dir', type = str, default = 'mirflickr', help = 'Directory with the ground-truth label files.')
    parser_data.add_argument('--query_dir', type = str, default = None, help = 'Directory with the query list files.')
    parser_data.add_argument('--dup_file', type = str, default = 'mirflickr/duplicates.txt', help = 'File containing a list of IDs of duplicate images.')
    parser_data.add_argument('--feature_dump', type = str, default = 'features.npy', help = 'Path to a dump of the feature matrix of the dataset as created by extract_features.py.')
    parser_display = parser.add_argument_group('Results Format')
    parser_display.add_argument('--show_sd', action = 'store_const', const = True, default = False, help = 'Show table with standard deviation of performance metrics.')
    parser_display.add_argument('--csv', action = 'store_const', const = True, default = False, help = 'Output results as CSV instead of a table.')
    parser_display.add_argument('--plot_precision', action = 'store_const', const = True, default = False, help = 'Plot Precision@k versus several values of k.')
    parser_aid = parser.add_argument_group('AID')
    parser_aid.add_argument('--aid_gamma', type = float, default = None,
                            help = 'Parameter gamma for distance adjustment function of AID. Larger values will require a more exact match of directions, while smaller values will lead to an adjustment of near-orthogonal samples as well.')
    parser_aid.add_argument('--aid_k', type = int, default = None, help = 'Number of top baseline results to be used for clustering by AID.')
    parser_aid.add_argument('--aid_n_clusters', type = int, default = None, help = 'Fixes the number of clusters generated by AID.')
    parser_aid.add_argument('--aid_max_clusters', type = int, default = 10, help = 'Maximum number of clusters generated by AID.')
    parser_clue = parser.add_argument_group('CLUE')
    parser_clue.add_argument('--clue_k', type = int, default = None, help = 'Number of top baseline results to be used for clustering by CLUE.')
    parser_clue.add_argument('--clue_max_clusters', type = int, default = 10, help = 'Maximum number of clusters generated by CLUE.')
    parser_clue.add_argument('--clue_T', type = float, default = None, help = 'N-Cut threshold for CLUE.')
    parser_hard_select = parser.add_argument_group('Hard Cluster Selection on the same clusters as AID')
    parser_hard_select.add_argument('--hard-select_k', type = int, default = None, help = 'Number of top baseline results to be used for clustering by Hard Cluster Selection.')
    parser_hard_select.add_argument('--hard-select_n_clusters', type = int, default = None, help = 'Fixes the number of clusters generated by Hard Cluster Selection.')
    parser_hard_select.add_argument('--hard-select_max_clusters', type = int, default = 10, help = 'Maximum number of clusters generated by Hard Cluster Selection.')
    args = parser.parse_args()
    
    # Parse algorithm-specific parameters
    algo_params = {}
    for arg_name, arg_value in vars(args).items():
        arg_components = arg_name.split('_', 1)
        if (len(arg_components) > 1) and (arg_value is not None):
            algo_name = arg_components[0].lower()
            if algo_name in ('aid', 'clue', 'hard-select'):
                if algo_name not in algo_params:
                    algo_params[algo_name] = {}
                algo_params[algo_name][arg_components[1]] = arg_value
    
    # Load features and queries
    queries = get_dataset_queries(args.gt_dir, args.query_dir, args.dup_file)
    if len(queries) == 0:
        print('Could not find any queries. Have you set --query_dir correctly?')
        exit()
    features = np.load(args.feature_dump)
    
    # Run benchmarks
    metrics = OrderedDict()
    precisions = OrderedDict()
    select_clusters_func = select_clusters_by_precision if args.multiple else select_best_cluster
    select_clusters = lambda query, clusters: select_clusters_func(query, [c[:args.num_preview] for c in clusters])
    for r in ptqdm(range(args.rounds), desc = 'Rounds', total = args.rounds):
        for method in args.methods:
            
            params = algo_params[method.lower()] if method.lower() in algo_params else {}
            retrieved = METHODS[method](features, queries, select_clusters, show_progress = True, **params)
            
            if method not in precisions:
                precisions[method] = []
            with Pool() as p:
                precisions[method].append(np.mean(list(p.starmap(
                    eval_metrics.precision_at_k,
                    ((range(1, 101), queries[qid]['relevant'], ret, set([queries[qid]['img_id']]) | (queries[qid]['ignore'] if 'ignore' in queries[qid] else set())) for qid, (ret, dist) in retrieved.items())
                )), axis = 0))
            
            if method not in metrics:
                metrics[method] = []
            metrics[method].append(eval_metrics.avg_query_metrics({ qid : {
                    'relevant' : query['relevant'],
                    'retrieved' : retrieved[qid][0],
                    'ignore' : set([query['img_id']]) | (query['ignore'] if 'ignore' in query else set())
                } for qid, query in queries.items() }, include_prec = False))
            for pk in (1, 10, 50, 100):
                metrics[method][-1]['P@{}'.format(pk)] = precisions[method][-1][pk - 1]
    
    print_metrics(OrderedDict((method, { metric: np.mean([m[metric] for m in lists]) for metric in lists[0].keys() }) for method, lists in metrics.items()), tabular = not args.csv)
    
    if args.show_sd:
        print('\nStandard Deviation:')
        print_metrics(OrderedDict((method, { metric: np.std([m[metric] for m in lists]) for metric in lists[0].keys() }) for method, lists in metrics.items()), tabular = not args.csv)
    
    if args.plot_precision:
        import matplotlib.pyplot as plt
        x = np.arange(1, 101)
        for (method, prec), col in zip(precisions.items(), itertools.cycle('bgyrcm')):
            y = np.mean(prec, axis = 0)
            yerr = 1.959964 * np.std(prec, axis = 0)
            plt.plot(x, y, linestyle = '-', c = col, label = method)
            if yerr.max() > 0.01:
                plt.plot(x, y + yerr, linestyle = ':', linewidth = 1, c = col)
                plt.plot(x, y - yerr, linestyle = ':', linewidth = 1, c = col)
            
        plt.xlabel('Retrieved Images')
        plt.ylabel('Precision')
        plt.grid(linestyle = '--')
        plt.legend()
        plt.show()
