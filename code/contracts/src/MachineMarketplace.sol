// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./common/Ownable.sol";
import {MachineAssetNFT, TransferGuardBlocked} from "./MachineAssetNFT.sol";
import {ITransferGuard} from "./interfaces/ITransferGuard.sol";

interface IERC20TransferFromLike {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

contract MachineMarketplace is Ownable {
    struct Listing {
        uint256 id;
        uint256 machineId;
        address seller;
        address paymentToken;
        uint256 price;
        uint64 expiry;
        bool active;
    }

    MachineAssetNFT public immutable machineAsset;

    uint256 public nextListingId = 1;

    mapping(uint256 => Listing) private _listings;
    mapping(uint256 => uint256) public activeListingIdByMachine;
    mapping(address => bool) public supportedPaymentToken;

    event ListingCreated(
        uint256 indexed listingId,
        uint256 indexed machineId,
        address indexed seller,
        address paymentToken,
        uint256 price,
        uint64 expiry
    );
    event ListingCancelled(uint256 indexed listingId, uint256 indexed machineId, address indexed cancelledBy);
    event ListingPurchased(
        uint256 indexed listingId,
        uint256 indexed machineId,
        address indexed buyer,
        address seller,
        address paymentToken,
        uint256 price
    );
    event PaymentTokenSupportUpdated(address indexed token, bool supported);

    constructor(address initialOwner, address machineAssetAddress, address[] memory supportedTokens) Ownable(initialOwner) {
        require(machineAssetAddress != address(0), "ZERO_MACHINE_ASSET");
        machineAsset = MachineAssetNFT(machineAssetAddress);

        for (uint256 index = 0; index < supportedTokens.length; index++) {
            _setSupportedPaymentToken(supportedTokens[index], true);
        }
    }

    function setSupportedPaymentToken(address token, bool supported) external onlyOwner {
        _setSupportedPaymentToken(token, supported);
    }

    function createListing(uint256 machineId, address paymentToken, uint256 price, uint64 expiry)
        external
        returns (uint256 listingId)
    {
        require(price > 0, "ZERO_PRICE");
        require(expiry > block.timestamp, "INVALID_EXPIRY");
        require(supportedPaymentToken[paymentToken], "UNSUPPORTED_PAYMENT_TOKEN");
        require(machineAsset.ownerOf(machineId) == msg.sender, "NOT_MACHINE_OWNER");
        require(_isMarketplaceApproved(machineId, msg.sender), "MARKETPLACE_NOT_APPROVED");

        _clearMachineListingIfInactive(machineId);
        require(activeListingIdByMachine[machineId] == 0, "ACTIVE_LISTING_EXISTS");

        _enforceTransferGuard(machineId, msg.sender, address(this));

        listingId = nextListingId;
        nextListingId += 1;

        _listings[listingId] = Listing({
            id: listingId,
            machineId: machineId,
            seller: msg.sender,
            paymentToken: paymentToken,
            price: price,
            expiry: expiry,
            active: true
        });
        activeListingIdByMachine[machineId] = listingId;

        emit ListingCreated(listingId, machineId, msg.sender, paymentToken, price, expiry);
    }

    function cancelListing(uint256 listingId) external {
        Listing storage listing = _listings[listingId];
        require(listing.id != 0, "LISTING_NOT_FOUND");
        require(listing.active, "LISTING_INACTIVE");

        address currentOwner = machineAsset.ownerOf(listing.machineId);
        require(msg.sender == listing.seller || msg.sender == currentOwner, "NOT_AUTHORIZED");

        listing.active = false;
        if (activeListingIdByMachine[listing.machineId] == listingId) {
            activeListingIdByMachine[listing.machineId] = 0;
        }

        emit ListingCancelled(listingId, listing.machineId, msg.sender);
    }

    function buyListing(uint256 listingId) external {
        Listing storage listing = _listings[listingId];
        require(listing.id != 0, "LISTING_NOT_FOUND");
        require(listing.active, "LISTING_INACTIVE");
        require(block.timestamp <= listing.expiry, "LISTING_EXPIRED");
        require(msg.sender != listing.seller, "SELLER_CANNOT_BUY");
        require(machineAsset.ownerOf(listing.machineId) == listing.seller, "SELLER_NOT_OWNER");
        require(_isMarketplaceApproved(listing.machineId, listing.seller), "MARKETPLACE_NOT_APPROVED");

        _enforceTransferGuard(listing.machineId, listing.seller, msg.sender);

        listing.active = false;
        if (activeListingIdByMachine[listing.machineId] == listingId) {
            activeListingIdByMachine[listing.machineId] = 0;
        }

        bool paymentSucceeded =
            IERC20TransferFromLike(listing.paymentToken).transferFrom(msg.sender, listing.seller, listing.price);
        require(paymentSucceeded, "PAYMENT_TRANSFER_FAILED");

        machineAsset.safeTransferFrom(listing.seller, msg.sender, listing.machineId);

        emit ListingPurchased(
            listingId,
            listing.machineId,
            msg.sender,
            listing.seller,
            listing.paymentToken,
            listing.price
        );
    }

    function getListing(uint256 listingId)
        external
        view
        returns (
            uint256 id,
            uint256 machineId,
            address seller,
            address paymentToken,
            uint256 price,
            uint64 expiry,
            bool active
        )
    {
        Listing memory listing = _listings[listingId];
        require(listing.id != 0, "LISTING_NOT_FOUND");
        return (
            listing.id,
            listing.machineId,
            listing.seller,
            listing.paymentToken,
            listing.price,
            listing.expiry,
            listing.active
        );
    }

    function _clearMachineListingIfInactive(uint256 machineId) internal {
        uint256 existingListingId = activeListingIdByMachine[machineId];
        if (existingListingId == 0) {
            return;
        }

        Listing storage existingListing = _listings[existingListingId];
        if (!existingListing.active || block.timestamp > existingListing.expiry) {
            existingListing.active = false;
            activeListingIdByMachine[machineId] = 0;
        }
    }

    function _isMarketplaceApproved(uint256 machineId, address seller) internal view returns (bool) {
        return machineAsset.getApproved(machineId) == address(this)
            || machineAsset.isApprovedForAll(seller, address(this));
    }

    function _enforceTransferGuard(uint256 machineId, address from, address to) internal view {
        address guard = machineAsset.transferGuard();
        if (guard == address(0)) {
            return;
        }
        (bool allowed, bytes32 reason) = ITransferGuard(guard).canTransfer(machineId, from, to);
        if (!allowed) {
            revert TransferGuardBlocked(machineId, reason);
        }
    }

    function _setSupportedPaymentToken(address token, bool supported) internal {
        require(token != address(0), "ZERO_TOKEN");
        supportedPaymentToken[token] = supported;
        emit PaymentTokenSupportUpdated(token, supported);
    }
}
