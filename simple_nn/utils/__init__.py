import six
from six.moves import cPickle as pickle
import numpy as np
from cffi import FFI
import os, sys
import types

def _gen_2Darray_for_ffi(arr, ffi, cdata="double"):
    # Function to generate 2D pointer for cffi  
    shape = arr.shape
    arr_p = ffi.new(cdata + " *[%d]" % shape[0])
    for i in range(shape[0]):
        arr_p[i] = ffi.cast(cdata + " *", arr[i].ctypes.data)
    return arr_p


def pickle_load(filename):
    with open(filename, 'rb') as fil:
        if six.PY2:
            return pickle.load(fil)
        elif six.PY3:
            return pickle.load(fil, encoding='latin1')


def _make_data_list(filename):
    data_list = list()
    with open(filename, 'r') as fil:
        for line in fil:
            data_list.append(line.strip())
    return data_list


def _make_full_featurelist(filelist, atom_types, feature_tag):
    data_list = _make_data_list(filelist)

    feature_list = dict()
    idx_list = dict()

    for item in atom_types:
        feature_list[item] = list()
        idx_list[item] = list()

    for i,item in enumerate(data_list):
        tmp_data = pickle_load(item)
        for jtem in atom_types:
            if jtem in tmp_data[feature_tag]:
                feature_list[jtem].append(tmp_data[feature_tag][jtem])
                idx_list[jtem].append([i]*tmp_data['N'][jtem])
                
    for item in atom_types:
        if len(feature_list[item]) > 0:
            feature_list[item] = np.concatenate(feature_list[item], axis=0)
            idx_list[item] = np.concatenate(idx_list[item], axis=0)

    return feature_list, idx_list


def _generate_scale_file(feature_list, atom_types, inp_size):
    scale = dict()
    for item in atom_types:
        scale[item] = np.zeros([2, inp_size[item]])

        if len(feature_list[item]) > 0:
            scale[item][0,:] = 0.5*(np.amax(feature_list[item], axis=0) + np.amin(feature_list[item], axis=0))
            scale[item][1,:] = 0.5*(np.amax(feature_list[item], axis=0) - np.amin(feature_list[item], axis=0))
            scale[item][1, scale[item][1,:] < 1e-15] = 1.
        else:
            scale[item][1,:] = 1.

    with open('scale_factor', 'wb') as fil:
        pickle.dump(scale, fil, pickle.HIGHEST_PROTOCOL)

    return scale

def _generate_gdf_file(feature_list, scale, atom_types, idx_list, sigma=0.02, modifier=None):
    ffi = FFI()
    ffi.cdef("""void calculate_gdf(double **, int, int, double, double *);""")
    lib = ffi.dlopen(os.path.join(os.path.dirname(os.path.realpath(__file__)) + "/libgdf.so"))

    gdf = dict()
    for item in atom_types:
        if len(feature_list[item]) > 0:
            scaled_feature = feature_list[item] - scale[item][0:1,:]
            scaled_feature /= scale[item][1:2,:]
            scaled_feature_p = _gen_2Darray_for_ffi(scaled_feature, ffi)

            temp_gdf = np.zeros([scaled_feature.shape[0]], dtype=np.float64, order='C')
            temp_gdf_p = ffi.cast("double *", temp_gdf.ctypes.data)

            lib.calculate_gdf(scaled_feature_p, scaled_feature.shape[0], scaled_feature.shape[1], sigma, temp_gdf_p)
            gdf[item] = np.squeeze(np.dstack(([temp_gdf, idx_list[item]])))
            if callable(modifier):
                gdf[item] = modifier(gdf[item])
            gdf[item][:,0] /= np.mean(gdf[item][:,0])

    with open('atomic_weights', 'wb') as fil:
        pickle.dump(gdf, fil, pickle.HIGHEST_PROTOCOL)

    return gdf

def preprocessing(filelist, atom_types, feature_tag, inp_size, \
                  calc_scale=True, get_atomic_weights=None, **kwarg):
    """
    get_atomic_weights:
        if this parameter is function, generate atomic weights using this function.
        else if this parameter is string, load atomic weights from file(its name is that string).
        otherwise, ValueError
    **kwarg:
        parameter for get_atomic_weights
    """
    feature_list, idx_list = _make_full_featurelist(filelist, atom_types, feature_tag)
    scale = None
    atomic_weights = None
    if calc_scale:
        scale = _generate_scale_file(feature_list, atom_types, inp_size)
    else:
        scale = pickle_load('./scale_factor')
    
    if callable(get_atomic_weights):
        atomic_weights = get_atomic_weights(feature_list, scale, atom_types, idx_list, **kwarg)
    elif isinstance(get_atomic_weights, six.string_types):
        atomic_weights = pickle_load(get_atomic_weights)

    return scale, atomic_weights

def compress_outcar(filename):
    """
    Compress VASP OUTCAR file for fast file-reading in ASE.
    Compressed file (tmp_comp_OUTCAR) is temporarily created in the current directory.

    supported properties:
    - atom types
    - lattice vector(cell)
    - free energy
    - force
    """
    comp_name = './tmp_comp_OUTCAR'

    with open(filename, 'r') as fil, open(comp_name, 'w') as res:
        minus_tag = 0
        line_tag = 0
        for line in fil:
            if 'POTCAR:' in line:
                res.write(line)
            elif 'ions per type' in line:
                res.write(line)
            elif 'direct lattice vectors' in line:
                res.write(line)
                minus_tag = 3
            elif 'FREE ENERGIE OF THE ION-ELECTRON SYSTEM' in line:
                res.write(line)
                minus_tag = 4
            elif 'POSITION          ' in line:
                res.write(line)
                line_tag = 3
            elif minus_tag > 0:
                res.write(line)
                minus_tag -= 1
            elif line_tag > 0:
                res.write(line)
                if '-------------------' in line:
                    line_tag -= 1

    return comp_name

def modified_sigmoid(gdf, b=150.0, c=1.0):
    gdf[:,0] = gdf[:,0] / (1.0 + np.exp(-b * gdf[:,0] + c))
    return gdf
